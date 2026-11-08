"""The MQTT ingest consumer.

Unit tests drive ``process`` with a fake paho client. The integration tests at
the end need a broker that allows anonymous clients (``MQTT_TEST_HOST``,
``eclipse-mosquitto`` with ``/mosquitto-no-auth.conf``) and are skipped
without one.
"""

import asyncio
import os
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

import orjson
import paho.mqtt.client as mqtt
import pytest
from sqlalchemy import func, select

from app.models import Event, Telemetry
from app.mqtt import ingest
from app.mqtt.ingest import BadMessage, Delivery, IngestConsumer, decode_events, decode_telemetry
from app.websocket_manager import manager
from tests.conftest import TestSessionLocal

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def reading(robot_id=None, seconds=0, **overrides):
    body = {
        "battery": 80.0,
        "temperature": 35.0,
        "speed": 1.0,
        "timestamp": (T0 + timedelta(seconds=seconds)).isoformat(),
    }
    if robot_id is not None:
        body["robot_id"] = robot_id
    return body | overrides


def payload(body) -> bytes:
    return orjson.dumps(body)


class FakeClient:
    def __init__(self):
        self.acked = []

    def ack(self, mid, qos):
        self.acked.append(mid)


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []

    async def record(rows, stream=None):
        sent.extend(rows)

    monkeypatch.setattr(manager, "broadcast_batch", record)
    return sent


@pytest.fixture
def consumer():
    c = IngestConsumer(session_factory=TestSessionLocal, batch_size=5, batch_wait=0.05)
    c.client = FakeClient()
    c.epoch = 1
    return c


def delivery(robot_id, body, mid, channel="telemetry", epoch=1):
    raw = body if isinstance(body, bytes) else payload(body)
    return Delivery(robot_id, channel, raw, mid, 1, epoch)


def event(message="Mission complete", seconds=0):
    return {"message": message, "timestamp": (T0 + timedelta(seconds=seconds)).isoformat()}


@pytest.fixture
def event_broadcasts(monkeypatch):
    sent = []

    async def record(payload, stream=None):
        sent.append(payload)

    monkeypatch.setattr(manager, "broadcast", record)
    return sent


async def event_count():
    async with TestSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(Event))).scalar_one()


async def stored_count():
    async with TestSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(Telemetry))).scalar_one()


# ── decoding ────────────────────────────────────────────────────────


def test_decode_takes_the_robot_from_the_topic():
    [r] = decode_telemetry(4, payload(reading()))
    assert r.robot_id == 4


def test_decode_accepts_a_batch():
    readings = decode_telemetry(4, payload([reading(4, 0), reading(seconds=1)]))
    assert [r.robot_id for r in readings] == [4, 4]


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        payload(reading(robot_id=5)),
        payload([reading(), "x"]),
        payload(reading(battery="full")),
        payload({k: v for k, v in reading().items() if k != "timestamp"}),
    ],
)
def test_decode_rejects_bad_messages(raw):
    with pytest.raises(BadMessage):
        decode_telemetry(4, raw)


def test_events_over_mqtt_need_a_timestamp():
    [e] = decode_events(4, payload(event()))
    assert (e.robot_id, e.message) == (4, "Mission complete")
    with pytest.raises(BadMessage):
        decode_events(4, payload({"message": "no time"}))


# ── processing ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_is_stored_broadcast_and_acked(consumer, broadcasts):
    await consumer.process([delivery(1, reading(seconds=i), mid=i + 1) for i in range(3)])
    assert await stored_count() == 3
    assert len(broadcasts) == 3
    assert consumer.client.acked == [1, 2, 3]


@pytest.mark.asyncio
async def test_redelivered_messages_are_acked_but_not_stored_twice(consumer, broadcasts):
    await consumer.process([delivery(1, reading(), mid=1)])
    await consumer.process([delivery(1, reading(), mid=2)])
    assert await stored_count() == 1
    assert len(broadcasts) == 1
    assert consumer.client.acked == [1, 2]
    assert consumer.stats["duplicates"] == 1


@pytest.mark.asyncio
async def test_bad_messages_are_acked_and_dropped(consumer, broadcasts):
    """Otherwise the broker would redeliver a poison message forever."""
    await consumer.process(
        [
            delivery(1, b"garbage", mid=1),
            delivery(1, reading(robot_id=2), mid=2),
            delivery(1, reading(), mid=3, channel="config/reported"),
            delivery(1, reading(seconds=5), mid=4),
        ]
    )
    assert await stored_count() == 1
    assert consumer.client.acked == [1, 2, 3, 4]
    assert consumer.stats["rejected"] == 3


@pytest.mark.asyncio
async def test_events_and_readings_share_a_batch(consumer, broadcasts, event_broadcasts):
    await consumer.process(
        [
            delivery(1, reading(), mid=1),
            delivery(1, event(), mid=2, channel="events"),
            delivery(1, event(), mid=3, channel="events"),  # redelivered
        ]
    )
    assert (await stored_count(), await event_count()) == (1, 1)
    assert len(broadcasts) == 1
    assert [e["message"] for e in event_broadcasts] == ["Mission complete"]
    assert consumer.client.acked == [1, 2, 3]
    assert consumer.stats["duplicates"] == 1


@pytest.mark.asyncio
async def test_acks_from_an_older_connection_are_skipped(consumer, broadcasts):
    await consumer.process([delivery(1, reading(), mid=7, epoch=0)])
    assert consumer.client.acked == []
    assert await stored_count() == 1


@pytest.mark.asyncio
async def test_nothing_is_acked_until_the_write_succeeds(consumer, broadcasts, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_RETRY_DELAY_SECONDS", 0.01)
    attempts = 0

    class FlakySession:
        async def __aenter__(self):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("database down")
            self.session = TestSessionLocal()
            return await self.session.__aenter__()

        async def __aexit__(self, *exc):
            return await self.session.__aexit__(*exc)

    consumer.session_factory = FlakySession
    task = asyncio.create_task(consumer.process([delivery(1, reading(), mid=1)]))
    await asyncio.sleep(0)
    assert consumer.client.acked == []
    await asyncio.wait_for(task, 10)
    assert attempts == 3
    assert consumer.client.acked == [1]
    assert await stored_count() == 1


@pytest.mark.asyncio
async def test_batches_close_on_size_or_age(consumer):
    consumer._loop = asyncio.get_running_loop()
    for i in range(7):
        consumer._queue.put_nowait(delivery(1, reading(seconds=i), mid=i))
    assert len(await consumer.next_batch()) == 5
    started = time.monotonic()
    assert len(await consumer.next_batch()) == 2
    assert time.monotonic() - started < 1


# ── against a real broker ───────────────────────────────────────────

MQTT_HOST = os.getenv("MQTT_TEST_HOST")


def _broker_up() -> bool:
    if not MQTT_HOST:
        return False
    try:
        socket.create_connection((MQTT_HOST, 1883), timeout=1).close()
        return True
    except OSError:
        return False


broker = pytest.mark.skipif(not _broker_up(), reason="needs MQTT_TEST_HOST")


def publish(robot_id, bodies):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
    client.connect(MQTT_HOST, 1883)
    client.loop_start()
    infos = [client.publish(f"fleet/{robot_id}/telemetry", payload(b), qos=1) for b in bodies]
    for info in infos:
        info.wait_for_publish(5)
    client.disconnect()
    client.loop_stop()


async def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def live_consumer(name, session_factory=TestSessionLocal):
    return IngestConsumer(
        host=MQTT_HOST,
        port=1883,
        username=name,
        password="",
        group=name,
        session_factory=session_factory,
    )


async def start(consumer):
    task = asyncio.create_task(consumer.run())
    await asyncio.wait_for(consumer.connected.wait(), 10)
    await asyncio.sleep(0.2)  # let the subscription land
    return task


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@broker
@pytest.mark.asyncio
async def test_end_to_end_through_the_broker(broadcasts):
    name = f"ingest-{uuid.uuid4().hex[:8]}"
    task = await start(live_consumer(name))
    try:
        publish(3, [reading(seconds=i) for i in range(50)])
        assert await wait_for(lambda: _count_is(50))
    finally:
        await stop(task)


@broker
@pytest.mark.asyncio
async def test_unacked_messages_survive_a_crash_and_queued_ones_a_restart(broadcasts):
    """The consumer dies holding an uncommitted batch, and more readings arrive
    while it is down. A new process with the same session gets all of them."""
    name = f"ingest-{uuid.uuid4().hex[:8]}"

    class Hang:
        async def __aenter__(self):
            await asyncio.Event().wait()

        async def __aexit__(self, *exc):
            return False

    crashed = await start(live_consumer(name, session_factory=Hang))
    publish(3, [reading(seconds=i) for i in range(20)])
    await asyncio.sleep(0.5)
    await stop(crashed)
    assert await stored_count() == 0

    publish(3, [reading(seconds=i) for i in range(20, 30)])

    task = await start(live_consumer(name))
    try:
        assert await wait_for(lambda: _count_is(30))
    finally:
        await stop(task)


async def _count_is(n):
    return await stored_count() == n
