"""MQTT ingest consumer: robot messages in, committed rows out.

The broker is the queue. Messages arrive with QoS 1 on a persistent session,
are written in micro-batches through the same function as the HTTP endpoint,
and are acknowledged only once their batch is committed. Anything not yet
committed when the process dies stays in the broker and is delivered again,
and because writes skip readings already stored, a redelivery is harmless.

paho runs its network loop in a background thread. Messages are handed to the
asyncio loop through a queue; the number of unacknowledged messages, and so
the queue, is bounded by the receive maximum sent to the broker.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import orjson
import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from pydantic import ValidationError

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.mqtt import topics
from app.schemas import TelemetryCreate
from app.services.telemetry_service import broadcast_payloads, store_readings
from app.websocket_manager import manager

logger = logging.getLogger(__name__)
settings = get_settings()

SESSION_EXPIRY_SECONDS = 7 * 24 * 3600
RECEIVE_MAXIMUM = 1000
MAX_RETRY_DELAY_SECONDS = 10.0


class BadMessage(ValueError):
    """A message that can never be stored. It is acknowledged and dropped."""


@dataclass(frozen=True)
class Delivery:
    robot_id: int
    channel: str
    payload: bytes
    mid: int
    qos: int
    # Which connection the message arrived on. A packet id is only meaningful
    # on the connection that delivered it, so acks for an older connection are
    # skipped; the broker redelivers those messages anyway.
    epoch: int


def decode_telemetry(robot_id: int, payload: bytes) -> list[TelemetryCreate]:
    """One reading (a JSON object) or several (a JSON array).

    The robot id comes from the topic, which the broker ACL ties to the
    robot's login, so a body naming another robot is refused.
    """
    try:
        body = orjson.loads(payload)
    except orjson.JSONDecodeError as exc:
        raise BadMessage(f"not JSON: {exc}") from None
    items = body if isinstance(body, list) else [body]
    readings = []
    for item in items:
        if not isinstance(item, dict):
            raise BadMessage("each reading must be an object")
        claimed = item.setdefault("robot_id", robot_id)
        if claimed != robot_id:
            raise BadMessage(f"reading for robot {claimed} on robot {robot_id}'s topic")
        try:
            readings.append(TelemetryCreate.model_validate(item))
        except ValidationError as exc:
            raise BadMessage(str(exc)) from None
    return readings


class IngestConsumer:
    def __init__(
        self,
        *,
        host: str = settings.mqtt_host,
        port: int = settings.mqtt_port,
        username: str = settings.mqtt_service_username,
        password: str = settings.telemetry_api_key,
        group: str = settings.mqtt_ingest_group,
        batch_size: int = settings.mqtt_batch_size,
        batch_wait: float = settings.mqtt_batch_wait_ms / 1000,
        session_factory=AsyncSessionLocal,
    ) -> None:
        self.host, self.port = host, port
        self.group = group
        self.batch_size = batch_size
        self.batch_wait = batch_wait
        self.session_factory = session_factory
        self.epoch = 0
        self.connected = asyncio.Event()
        self.stats = {"stored": 0, "duplicates": 0, "rejected": 0, "batches": 0}
        self._queue: asyncio.Queue[Delivery] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=username,
            protocol=mqtt.MQTTv5,
            manual_ack=True,
        )
        self.client.username_pw_set(username, password)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # ── paho thread ─────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.error("MQTT connection refused: %s", reason_code)
            return
        self.epoch += 1
        client.subscribe(topics.shared_subscription(self.group, topics.TELEMETRY), qos=1)
        logger.info(
            "MQTT connected to %s:%d (session present: %s)",
            self.host,
            self.port,
            flags.session_present,
        )
        self._loop.call_soon_threadsafe(self.connected.set)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        logger.warning("MQTT disconnected: %s", reason_code)
        self._loop.call_soon_threadsafe(self.connected.clear)

    def _on_message(self, client, userdata, message) -> None:
        parsed = topics.parse(message.topic)
        robot_id, channel = parsed if parsed else (0, "")
        delivery = Delivery(
            robot_id, channel, message.payload, message.mid, message.qos, self.epoch
        )
        self._loop.call_soon_threadsafe(self._queue.put_nowait, delivery)

    # ── asyncio side ────────────────────────────────────────────────

    async def run(self) -> None:
        """Connect and consume until cancelled."""
        self._loop = asyncio.get_running_loop()
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = SESSION_EXPIRY_SECONDS
        props.ReceiveMaximum = RECEIVE_MAXIMUM
        # clean_start=False on every connection, including the first: paho's
        # default starts the first one clean, which would discard everything
        # the broker queued while this process was down.
        self.client.connect_async(
            self.host, self.port, keepalive=30, clean_start=False, properties=props
        )
        self.client.loop_start()
        try:
            while True:
                await self.process(await self.next_batch())
        finally:
            self.client.disconnect()
            self.client.loop_stop()

    async def next_batch(self) -> list[Delivery]:
        """Wait for one message, then collect more until the batch is full or old."""
        batch = [await self._queue.get()]
        deadline = asyncio.get_running_loop().time() + self.batch_wait
        while len(batch) < self.batch_size:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(self._queue.get(), remaining))
            except asyncio.TimeoutError:
                break
        return batch

    async def process(self, batch: list[Delivery]) -> None:
        """Store a batch, broadcast what is new, then acknowledge it."""
        readings: list[TelemetryCreate] = []
        for delivery in batch:
            try:
                if delivery.channel != topics.TELEMETRY:
                    raise BadMessage(f"unexpected topic channel {delivery.channel!r}")
                readings.extend(decode_telemetry(delivery.robot_id, delivery.payload))
            except BadMessage as exc:
                self.stats["rejected"] += 1
                logger.warning("Dropping message from robot %d: %s", delivery.robot_id, exc)

        inserted = await self._store(readings) if readings else []
        self.stats["batches"] += 1
        self.stats["stored"] += len(inserted)
        self.stats["duplicates"] += len(readings) - len(inserted)

        if inserted:
            await manager.broadcast_batch(broadcast_payloads(inserted))
        self.ack(batch)

    async def _store(self, readings: list[TelemetryCreate]):
        """Write until it succeeds. Nothing is acknowledged in the meantime,
        so the broker keeps queueing and no message is lost."""
        delay = 0.5
        while True:
            try:
                async with self.session_factory() as db:
                    return await store_readings(db, readings)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Storing %d readings failed; retrying in %.1fs", len(readings), delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)

    def ack(self, batch: list[Delivery]) -> None:
        for delivery in batch:
            if delivery.qos > 0 and delivery.epoch == self.epoch:
                self.client.ack(delivery.mid, delivery.qos)
