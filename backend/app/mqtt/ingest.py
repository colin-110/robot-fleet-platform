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
from dataclasses import dataclass, field
from datetime import datetime, timezone

import orjson
import paho.mqtt.client as mqtt
from fastapi import HTTPException
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from pydantic import ValidationError

from app import presence
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.mqtt import topics
from app.repositories.event_repo import insert_events
from app.schemas import CommandStatusUpdate, EventCreate, TelemetryCreate
from app.services.command_service import CommandService
from app.services.event_service import broadcast_events
from app.services.telemetry_service import (
    broadcast_payloads,
    persist_readings,
    resolve_timestamp,
)
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


SUBSCRIBED_CHANNELS = (topics.TELEMETRY, topics.EVENTS, topics.COMMAND_STATUS)


def _decode(robot_id: int, payload: bytes, model):
    """One item (a JSON object) or several (a JSON array) of ``model``.

    The robot id comes from the topic, which the broker ACL ties to the
    robot's login, so a body naming another robot is refused.
    """
    try:
        body = orjson.loads(payload)
    except orjson.JSONDecodeError as exc:
        raise BadMessage(f"not JSON: {exc}") from None
    items = body if isinstance(body, list) else [body]
    decoded = []
    for item in items:
        if not isinstance(item, dict):
            raise BadMessage("each item must be an object")
        claimed = item.setdefault("robot_id", robot_id)
        if claimed != robot_id:
            raise BadMessage(f"message for robot {claimed} on robot {robot_id}'s topic")
        try:
            decoded.append(model.model_validate(item))
        except ValidationError as exc:
            raise BadMessage(str(exc)) from None
    return decoded


def decode_telemetry(robot_id: int, payload: bytes) -> list[TelemetryCreate]:
    return _decode(robot_id, payload, TelemetryCreate)


def decode_events(robot_id: int, payload: bytes) -> list[EventCreate]:
    """Events over MQTT must carry their device timestamp: a redelivered event
    stamped with the receive time would look new and be stored twice."""
    events = _decode(robot_id, payload, EventCreate)
    if any(e.timestamp is None for e in events):
        raise BadMessage("events sent over MQTT need a timestamp")
    return events


@dataclass(frozen=True)
class StatusReport:
    robot_id: int
    command_id: str
    update: CommandStatusUpdate


def decode_command_status(robot_id: int, payload: bytes) -> StatusReport:
    """``{"id": <command id>, "status": ..., "error_code"?, "error_message"?, "result"?}``"""
    try:
        body = orjson.loads(payload)
    except orjson.JSONDecodeError as exc:
        raise BadMessage(f"not JSON: {exc}") from None
    if not isinstance(body, dict) or not isinstance(body.get("id"), str):
        raise BadMessage("a command status needs the command id")
    try:
        update = CommandStatusUpdate.model_validate(body)
    except ValidationError as exc:
        raise BadMessage(str(exc)) from None
    return StatusReport(robot_id, body["id"], update)


@dataclass
class Decoded:
    readings: list[TelemetryCreate] = field(default_factory=list)
    events: list[EventCreate] = field(default_factory=list)
    # Presence messages and "this robot sent something", in arrival order.
    # Order matters: a robot's last reading and its will often share a batch,
    # and applying the reading after the will would bring it back online.
    presence: list[tuple[int, tuple[bool, str | None] | None]] = field(default_factory=list)
    statuses: list[StatusReport] = field(default_factory=list)


def decode_presence(payload: bytes) -> tuple[bool, str | None]:
    parsed = presence.parse(payload)
    if parsed is None:
        raise BadMessage("presence must be online/offline or {online, conn}")
    return parsed


async def broadcast_presence(robot_id: int) -> None:
    state = presence.get(robot_id)
    payload = {
        "type": "PRESENCE",
        "robot_id": robot_id,
        "online": state.online,
        "since": state.since.isoformat().replace("+00:00", "Z"),
    }
    await manager.broadcast(payload)


def relay_publisher(client):
    """Callable the WebSocket manager uses to relay one broadcast."""

    def publish(payload: bytes) -> None:
        client.publish(topics.LIVE, payload, qos=0)

    return publish


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
        self.stats = {
            "stored": 0,
            "events": 0,
            "statuses": 0,
            "duplicates": 0,
            "rejected": 0,
            "batches": 0,
        }
        self._queue: asyncio.Queue[Delivery] = asyncio.Queue()
        # Called with a robot id whenever a robot is seen coming online.
        self.on_robot_online: list = []
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
        subscriptions = [
            (topics.shared_subscription(self.group, c), 1) for c in SUBSCRIBED_CHANNELS
        ]
        # Presence is state, not a stream, and is read from retained messages,
        # which MQTT 5 never delivers on shared subscriptions. Every process
        # subscribes to it directly.
        subscriptions.append((f"{topics.ROOT}/+/{topics.PRESENCE}", 1))
        if settings.websocket_backend == "mqtt":
            subscriptions.append((topics.LIVE, 0))
        client.subscribe(subscriptions)
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
        if message.topic == topics.LIVE:
            # QoS 0 and not stored: straight to this process's dashboards.
            self._loop.call_soon_threadsafe(manager.fan_out_relayed, message.payload)
            return
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
        decoded = Decoded()
        for delivery in batch:
            try:
                if delivery.channel == topics.TELEMETRY:
                    decoded.readings.extend(decode_telemetry(delivery.robot_id, delivery.payload))
                    decoded.presence.append((delivery.robot_id, None))
                elif delivery.channel == topics.EVENTS:
                    decoded.events.extend(decode_events(delivery.robot_id, delivery.payload))
                elif delivery.channel == topics.COMMAND_STATUS:
                    decoded.statuses.append(
                        decode_command_status(delivery.robot_id, delivery.payload)
                    )
                elif delivery.channel == topics.PRESENCE:
                    decoded.presence.append((delivery.robot_id, decode_presence(delivery.payload)))
                else:
                    raise BadMessage(f"unexpected topic channel {delivery.channel!r}")
            except BadMessage as exc:
                self.stats["rejected"] += 1
                logger.warning("Dropping message from robot %d: %s", delivery.robot_id, exc)

        readings, events = ([], [])
        if decoded.readings or decoded.events:
            readings, events = await self._store(decoded)
        self.stats["batches"] += 1
        self.stats["stored"] += len(readings)
        self.stats["events"] += len(events)
        self.stats["duplicates"] += (
            len(decoded.readings) + len(decoded.events) - len(readings) - len(events)
        )

        changed = []
        for robot_id, message in decoded.presence:
            if message is None:
                # A robot that is sending is connected, whatever an older will said.
                news = presence.saw_traffic(robot_id)
            else:
                news = presence.apply(robot_id, *message)
            if news:
                changed.append(robot_id)

        if readings:
            await manager.broadcast_batch(broadcast_payloads(readings))
        if events:
            await broadcast_events(events)
        for report in decoded.statuses:
            await self._apply_status(report)
        for robot_id in dict.fromkeys(changed):
            await broadcast_presence(robot_id)
            if presence.get(robot_id).online:
                for callback in self.on_robot_online:
                    callback(robot_id)
        self.ack(batch)

    async def _store(self, decoded: Decoded):
        """Write in one transaction until it succeeds. Nothing is acknowledged
        in the meantime, so the broker keeps queueing and no message is lost."""
        delay = 0.5
        while True:
            try:
                async with self.session_factory() as db:
                    readings = await persist_readings(db, decoded.readings)
                    now = datetime.now(timezone.utc)
                    events = await insert_events(
                        db, [(e, resolve_timestamp(e.timestamp, now)) for e in decoded.events]
                    )
                    await db.commit()
                    return readings, events
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Storing a batch failed; retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)

    async def _apply_status(self, report: StatusReport) -> None:
        """Advance a command through the same state machine as the HTTP PATCH.

        A refused transition (wrong robot, unknown command, a late or replayed
        status) is final and dropped. Only a database failure is retried, and
        until it succeeds nothing in the batch is acknowledged.
        """
        delay = 0.5
        while True:
            try:
                async with self.session_factory() as db:
                    service = CommandService(db)
                    if await service.get_robot_id(report.command_id) != report.robot_id:
                        raise HTTPException(403, "command belongs to another robot")
                    await service.update_status(report.command_id, report.update)
                self.stats["statuses"] += 1
                return
            except HTTPException as exc:
                self.stats["rejected"] += 1
                logger.info(
                    "Ignoring status %s for command %s from robot %d: %s",
                    report.update.status,
                    report.command_id,
                    report.robot_id,
                    exc.detail,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Applying a command status failed; retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)

    def ack(self, batch: list[Delivery]) -> None:
        for delivery in batch:
            if delivery.qos > 0 and delivery.epoch == self.epoch:
                self.client.ack(delivery.mid, delivery.qos)
