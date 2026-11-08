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
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from sqlalchemy import func, select

from app import presence
from app.models import Event, StoredWatermark, Telemetry
from app.mqtt import ingest, wire
from app.mqtt.commands import CommandPublisher
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
        self.published = []

    def ack(self, mid, qos):
        self.acked.append(mid)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def stored(self):
        """The watermarks published, as (topic, stream, seq)."""
        return [(t, *wire.decode_stored(p)) for t, p, _, retain in self.published if retain]


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
# ── agent envelopes ─────────────────────────────────────────────────


def envelope(channel, items, seq=1, floor=1, sent=None, stream="s1"):
    """A CBOR envelope as the agent sends it."""
    sent_ms = wire.epoch_ms(sent or datetime.now(timezone.utc))
    packed = [wire.pack_item(channel, i) for i in items]
    return wire.encode_envelope(channel, wire.Envelope(stream, seq, floor, sent_ms, packed))


def env_delivery(robot_id, channel, items, mid, **kwargs):
    raw = envelope(channel, items, **kwargs)
    return Delivery(robot_id, channel, raw, mid, 1, 1, wire.CONTENT_TYPE)


def agent_reading(seconds=0, **overrides):
    values = {
        "timestamp": T0 + timedelta(seconds=seconds),
        "battery": 80.0,
        "temperature": 35.0,
        "speed": 1.0,
    }
    return values | overrides


async def test_envelopes_are_stored_like_json(consumer, broadcasts, event_broadcasts):
    batch = [
        env_delivery(3, "telemetry", [agent_reading(0), agent_reading(1, status="CHARGING")], 1),
        env_delivery(3, "events", [{"timestamp": T0, "message": "Docked"}], 2),
    ]
    await consumer.process(batch)
    assert await stored_count() == 2
    assert await event_count() == 1
    assert [r["status"] for r in broadcasts] == [None, "CHARGING"]
    assert broadcasts[1]["battery"] == 80.0
    assert consumer.client.acked == [1, 2]


async def test_a_bad_item_does_not_sink_its_envelope(consumer, broadcasts):
    # A reading without a battery fails validation; its neighbours are kept.
    items = [agent_reading(0), {"timestamp": T0 + timedelta(seconds=1)}, agent_reading(2)]
    await consumer.process([env_delivery(3, "telemetry", items, 1)])
    assert await stored_count() == 2
    assert consumer.stats["rejected"] == 1
    assert consumer.client.acked == [1]


async def test_a_malformed_envelope_is_dropped(consumer, broadcasts):
    bad = Delivery(3, "telemetry", b"\xff\x00", 1, 1, 1, wire.CONTENT_TYPE)
    await consumer.process([bad])
    assert await stored_count() == 0
    assert consumer.stats["rejected"] == 1
    assert consumer.client.acked == [1]


async def test_a_fast_clock_is_corrected_without_collapsing_readings(consumer, broadcasts):
    now = datetime.now(timezone.utc)
    robot_now = now + timedelta(hours=1)
    items = [agent_reading(0, timestamp=robot_now - timedelta(seconds=s)) for s in (3, 2, 1)]
    await consumer.process([env_delivery(3, "telemetry", items, 1, sent=robot_now)])

    async with TestSessionLocal() as db:
        stamps = (
            (await db.execute(select(Telemetry.timestamp).order_by(Telemetry.timestamp)))
            .scalars()
            .all()
        )
    assert len(stamps) == 3
    assert [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False)] == [1.0, 1.0]
    # Shifted to the receive time, not left an hour ahead.
    assert abs((stamps[-1] - (now - timedelta(seconds=1))).total_seconds()) < 2


async def test_a_slow_clock_is_left_alone(consumer, broadcasts):
    # An old send time is also what a message that waited in the broker looks like.
    sent = T0 + timedelta(seconds=10)
    await consumer.process([env_delivery(3, "telemetry", [agent_reading(5)], 1, sent=sent)])
    async with TestSessionLocal() as db:
        stored = (await db.execute(select(Telemetry.timestamp))).scalar_one()
    assert stored == T0 + timedelta(seconds=5)


# ── stored watermarks ──────────────────────────────────────────────


def progress(first, last, floor=1, stream="s1"):
    return ingest.Progress(3, stream, first, last, floor)


@pytest.mark.parametrize(
    "current, p, expected",
    [
        (None, progress(1, 3), ("s1", 3)),
        (("s1", 3), progress(4, 6), ("s1", 6)),
        # Beyond a gap: stored, but the watermark waits for the gap to fill.
        (("s1", 3), progress(6, 8), ("s1", 3)),
        # A resend that overlaps what is stored.
        (("s1", 5), progress(2, 9), ("s1", 9)),
        # Pure duplicates change nothing.
        (("s1", 9), progress(2, 4), ("s1", 9)),
        # The robot no longer has anything below its floor.
        (("s1", 3), progress(10, 12, floor=10), ("s1", 12)),
        # A new outbox starts from zero.
        (("s1", 50), progress(1, 2, stream="s2"), ("s2", 2)),
        (("s1", 50), progress(3, 4, stream="s2"), ("s2", 0)),
    ],
)
def test_advance(current, p, expected):
    assert ingest.advance(current, p) == expected


def items(n, start=0):
    return [agent_reading(start + i) for i in range(n)]


async def test_the_watermark_is_saved_and_published_before_the_ack(consumer, broadcasts):
    await consumer.process(
        [
            env_delivery(3, "telemetry", items(3), 1, seq=1),
            env_delivery(3, "telemetry", items(2, 3), 2, seq=4),
        ]
    )
    assert consumer.client.stored() == [("fleet/3/stored", "s1", 5)]
    async with TestSessionLocal() as db:
        mark = await db.get(StoredWatermark, 3)
    assert (mark.stream, mark.seq) == ("s1", 5)
    assert consumer.client.acked == [1, 2]


async def test_a_gap_holds_the_watermark_until_it_is_filled(consumer, broadcasts):
    await consumer.process([env_delivery(3, "telemetry", items(2), 1, seq=1)])
    await consumer.process([env_delivery(3, "telemetry", items(2, 4), 2, seq=5)])
    assert consumer.client.stored()[-1][2] == 2
    assert consumer.stats["gaps"] == 1
    # The readings beyond the gap were stored all the same.
    assert await stored_count() == 4

    # The robot resends everything above 2.
    await consumer.process([env_delivery(3, "telemetry", items(4, 2), 3, seq=3)])
    assert consumer.client.stored()[-1][2] == 6
    assert await stored_count() == 6


async def test_the_watermark_survives_a_restart(consumer, broadcasts):
    await consumer.process([env_delivery(3, "telemetry", items(3), 1, seq=1)])

    restarted = IngestConsumer(session_factory=TestSessionLocal)
    restarted.client = FakeClient()
    restarted.epoch = 1
    await restarted.process([env_delivery(3, "telemetry", items(1, 3), 1, seq=4)])
    assert restarted.client.stored() == [("fleet/3/stored", "s1", 4)]


async def test_duplicates_are_confirmed_again(consumer, broadcasts):
    # The robot resends because it missed the confirmation; it must hear again.
    batch = [env_delivery(3, "telemetry", items(3), 1, seq=1)]
    await consumer.process(batch)
    await consumer.process(batch)
    assert [seq for _, _, seq in consumer.client.stored()] == [3, 3]


async def test_items_the_robot_gave_up_are_counted(consumer, broadcasts):
    await consumer.process([env_delivery(3, "telemetry", items(2), 1, seq=1)])
    await consumer.process([env_delivery(3, "telemetry", items(2, 9), 2, seq=10, floor=10)])
    assert consumer.stats["given_up"] == 7
    assert consumer.client.stored()[-1][2] == 11


async def test_rejected_items_still_count_as_processed(consumer, broadcasts):
    await consumer.process([env_delivery(3, "telemetry", [{"timestamp": T0}], 1, seq=1)])
    assert consumer.stats["rejected"] == 1
    assert consumer.client.stored() == [("fleet/3/stored", "s1", 1)]


async def test_json_messages_publish_no_watermark(consumer, broadcasts):
    await consumer.process([delivery(3, reading(), 1)])
    assert consumer.client.published == []


async def test_command_status_envelopes_need_an_id(consumer, broadcasts):
    await consumer.process([env_delivery(3, "commands/status", [{"status": "ACKNOWLEDGED"}], 1)])
    assert consumer.stats["rejected"] == 1


async def test_presence_changes_are_broadcast(consumer, broadcasts, event_broadcasts):
    await consumer.process(
        [
            delivery(1, b'{"online": true, "conn": "c1"}', mid=1, channel="presence"),
            delivery(2, b"offline", mid=2, channel="presence"),
            delivery(3, b"sideways", mid=3, channel="presence"),
        ]
    )
    assert presence.get(1).online and presence.is_offline(2)
    assert [(e["robot_id"], e["online"]) for e in event_broadcasts] == [(1, True), (2, False)]
    assert consumer.client.acked == [1, 2, 3]
    assert consumer.stats["rejected"] == 1

    # The will fires; then robot 2 turns out to be sending again.
    await consumer.process(
        [
            delivery(1, b'{"online": false, "conn": "c1"}', mid=4, channel="presence"),
            delivery(2, reading(), mid=5),
        ]
    )
    assert presence.is_offline(1) and not presence.is_offline(2)
    assert [(e["robot_id"], e["online"]) for e in event_broadcasts[2:]] == [(1, False), (2, True)]


@pytest.mark.asyncio
async def test_a_will_after_the_last_reading_in_one_batch_wins(
    consumer, broadcasts, event_broadcasts
):
    """A robot that dies sends its last reading and then its will, often within
    one batch. Presence follows arrival order, so the robot ends up offline."""
    await consumer.process([delivery(1, b"online", mid=1, channel="presence")])
    await consumer.process(
        [
            delivery(1, reading(), mid=2),
            delivery(1, b"offline", mid=3, channel="presence"),
        ]
    )
    assert presence.is_offline(1)

    # The other way round, a reading after the will means it is back.
    await consumer.process(
        [
            delivery(1, b"offline", mid=4, channel="presence"),
            delivery(1, reading(seconds=1), mid=5),
        ]
    )
    assert not presence.is_offline(1)


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
async def test_envelopes_are_recognised_by_their_content_type(broadcasts):
    name = f"ingest-{uuid.uuid4().hex[:8]}"
    task = await start(live_consumer(name))
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
        client.connect(MQTT_HOST, 1883)
        client.loop_start()
        props = Properties(PacketTypes.PUBLISH)
        props.ContentType = wire.CONTENT_TYPE
        body = envelope("telemetry", [agent_reading(i) for i in range(20)])
        client.publish("fleet/3/telemetry", body, qos=1, properties=props).wait_for_publish(5)
        client.disconnect()
        client.loop_stop()
        assert await wait_for(lambda: _count_is(20))
    finally:
        await stop(task)


@broker
@pytest.mark.asyncio
async def test_the_connection_survives_publishing_watermarks(broadcasts):
    # Wired as the app does it: the command publisher sees the PUBACK of
    # everything published on the connection, watermarks included. It once
    # raised on those before its first command, which killed paho's thread.
    name = f"ingest-{uuid.uuid4().hex[:8]}"
    consumer = live_consumer(name)
    CommandPublisher(consumer.client)
    task = await start(consumer)
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
        client.connect(MQTT_HOST, 1883)
        client.loop_start()
        props = Properties(PacketTypes.PUBLISH)
        props.ContentType = wire.CONTENT_TYPE
        for seq in (1, 6):
            body = envelope("telemetry", [agent_reading(seq + i) for i in range(5)], seq=seq)
            client.publish("fleet/3/telemetry", body, qos=1, properties=props).wait_for_publish(5)
            assert await wait_for(lambda n=seq + 4: _count_is(n))
        client.disconnect()
        client.loop_stop()
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


@broker
@pytest.mark.asyncio
async def test_broadcasts_are_relayed_to_every_instance(monkeypatch):
    """Two API instances: a broadcast on one reaches a dashboard on the other."""
    from app.config import get_settings
    from app.mqtt.ingest import relay_publisher
    from app.websocket_manager import ConnectionManager

    monkeypatch.setattr(get_settings(), "websocket_backend", "mqtt")
    other_instance = ConnectionManager()
    monkeypatch.setattr(ingest, "manager", other_instance)  # what the receiving side fans out to

    received = []

    class Socket:
        async def accept(self):
            pass

        async def send_json(self, data):
            received.append(data)

    await other_instance.connect(Socket())
    task = await start(live_consumer(f"relay-{uuid.uuid4().hex[:8]}"))
    sender = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
    sender.connect(MQTT_HOST, 1883)
    sender.loop_start()
    try:
        sending_instance = ConnectionManager()
        sending_instance.relay = relay_publisher(sender)
        await sending_instance.broadcast_batch([{"robot_id": 1, "battery": 50.0}])

        async def arrived():
            return received == [{"robot_id": 1, "battery": 50.0}]

        assert await wait_for(arrived, timeout=5)
    finally:
        sender.disconnect()
        sender.loop_stop()
        await stop(task)
