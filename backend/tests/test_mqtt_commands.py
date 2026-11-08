"""Commands pushed over MQTT.

Unit tests use a fake paho client whose PUBACK the test controls. The tests at
the end run against a real broker (``MQTT_TEST_HOST``) and are skipped
without one.
"""

import asyncio
import os
import socket
import time
import uuid

import orjson
import paho.mqtt.client as mqtt
import pytest
from sqlalchemy import select

from app.models import RobotCommand
from app.mqtt import commands
from app.mqtt.commands import CommandPublisher
from app.mqtt.ingest import Delivery, IngestConsumer
from app.websocket_manager import manager
from tests.conftest import TestSessionLocal


class FakeInfo:
    def __init__(self, mid):
        self.mid = mid


class FakeClient:
    """Publishes are recorded; the broker's answer is ``reply`` (None: silence)."""

    def __init__(self, reply=0):
        self.reply = reply
        self.published = []
        self.on_publish = None

    def publish(self, topic, payload, qos=0):
        mid = len(self.published) + 1
        self.published.append((topic, orjson.loads(payload)))
        if self.reply is not None:
            loop = asyncio.get_running_loop()
            loop.call_soon(self.on_publish, self, None, mid, self.reply, None)
        return FakeInfo(mid)


def test_pubacks_before_the_first_command_are_ignored():
    # Created outside a running loop, so nothing is bound yet. A PUBACK for
    # something else published on the connection must not raise: an exception
    # in a paho callback kills its network thread.
    publisher = CommandPublisher(FakeClient(reply=None))
    publisher._on_publish(None, None, 1, 0, None)


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []

    async def record(payload, stream=None):
        sent.append(payload)

    monkeypatch.setattr(manager, "broadcast", record)
    return sent


@pytest.fixture
def install(monkeypatch):
    """Install a publisher the way the lifespan does."""

    def _install(client):
        publisher = CommandPublisher(client, session_factory=TestSessionLocal)
        publisher.bind(asyncio.get_running_loop())
        # call_soon_threadsafe is what paho's thread uses; call_soon is fine here.
        monkeypatch.setattr(commands, "publisher", publisher)
        return publisher

    yield _install
    commands.publisher = None


async def status_of(command_id):
    async with TestSessionLocal() as db:
        result = await db.execute(select(RobotCommand.status).where(RobotCommand.id == command_id))
        return result.scalar_one()


async def create(client, robot_id=1, command_type="RETURN_TO_BASE"):
    response = await client.post(
        f"/api/v1/commands/{robot_id}", json={"command_type": command_type}
    )
    assert response.status_code == 200
    return response.json()["payload"]["command_id"]


async def settle(publisher):
    for _ in range(50):
        if not publisher._tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("push tasks did not finish")


@pytest.mark.asyncio
async def test_new_command_is_pushed_and_dispatched(client, install, broadcasts):
    fake = FakeClient(reply=0)
    publisher = install(fake)
    command_id = await create(client, robot_id=4)
    await settle(publisher)

    [(topic, body)] = fake.published
    assert topic == "fleet/4/commands"
    assert body["id"] == command_id and body["command_type"] == "RETURN_TO_BASE"
    assert await status_of(command_id) == "DISPATCHED"
    assert any(b.get("status") == "DISPATCHED" for b in broadcasts)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [0x10, 0x87, None])
async def test_command_stays_pending_unless_it_reached_a_queue(
    client, install, broadcasts, monkeypatch, reply
):
    """0x10: no robot session yet. 0x87: not authorised. None: no answer."""
    monkeypatch.setattr(commands, "ACK_TIMEOUT_SECONDS", 0.05)
    publisher = install(FakeClient(reply=reply))
    command_id = await create(client)
    await settle(publisher)
    assert await status_of(command_id) == "PENDING"


@pytest.mark.asyncio
async def test_pending_commands_are_pushed_when_the_robot_comes_online(client, install, broadcasts):
    fake = FakeClient(reply=0x10)
    publisher = install(fake)
    first, second = await create(client, 2), await create(client, 2, "RESUME")
    await create(client, 3)
    await settle(publisher)

    fake.reply = 0
    assert await publisher.push_pending(2) == 2
    assert [body["id"] for _, body in fake.published[-2:]] == [first, second]
    assert (await status_of(first), await status_of(second)) == ("DISPATCHED", "DISPATCHED")


@pytest.mark.asyncio
async def test_coming_online_triggers_the_push(install, broadcasts):
    seen = []
    consumer = IngestConsumer(session_factory=TestSessionLocal)
    consumer.client = type("C", (), {"ack": lambda *a: None})()
    consumer.on_robot_online.append(seen.append)
    await consumer.process([Delivery(5, "presence", b"online", 1, 1, 0)])
    await consumer.process([Delivery(5, "presence", b"online", 2, 1, 0)])  # not a change
    await consumer.process([Delivery(6, "presence", b"offline", 3, 1, 0)])
    assert seen == [5]


@pytest.mark.asyncio
async def test_robot_may_acknowledge_before_the_dispatch_lands(client):
    """The push can reach the robot before the server processes the PUBACK."""
    command_id = await create(client)
    response = await client.patch(
        f"/api/v1/commands/{command_id}/status", json={"status": "ACKNOWLEDGED"}
    )
    assert response.status_code == 200
    assert response.json()["payload"]["status"] == "ACKNOWLEDGED"


def status_report(command_id, robot_id, status, mid, **extra):
    body = orjson.dumps({"id": command_id, "status": status, **extra})
    return Delivery(robot_id, "commands/status", body, mid, 1, 0)


@pytest.fixture
def status_consumer():
    consumer = IngestConsumer(session_factory=TestSessionLocal)
    consumer.acked = []
    consumer.client = type("C", (), {"ack": lambda self, mid, qos: consumer.acked.append(mid)})()
    return consumer


@pytest.mark.asyncio
async def test_robot_reports_command_progress_over_mqtt(client, status_consumer, broadcasts):
    command_id = await create(client, robot_id=4)
    for mid, status in enumerate(["ACKNOWLEDGED", "EXECUTING", "COMPLETED"], start=1):
        await status_consumer.process([status_report(command_id, 4, status, mid)])
    assert await status_of(command_id) == "COMPLETED"
    assert status_consumer.acked == [1, 2, 3]
    assert status_consumer.stats["statuses"] == 3


@pytest.mark.asyncio
async def test_replayed_status_is_harmless(client, status_consumer, broadcasts):
    command_id = await create(client, robot_id=4)
    await status_consumer.process([status_report(command_id, 4, "ACKNOWLEDGED", 1)])
    await status_consumer.process([status_report(command_id, 4, "ACKNOWLEDGED", 2)])
    assert await status_of(command_id) == "ACKNOWLEDGED"
    assert status_consumer.acked == [1, 2]


@pytest.mark.asyncio
async def test_robot_cannot_report_on_another_robots_command(client, status_consumer, broadcasts):
    command_id = await create(client, robot_id=4)
    await status_consumer.process([status_report(command_id, 5, "FAILED", 1)])
    assert await status_of(command_id) == "PENDING"
    assert status_consumer.acked == [1]
    assert status_consumer.stats["rejected"] == 1


@pytest.mark.asyncio
async def test_invalid_and_malformed_reports_are_dropped(client, status_consumer, broadcasts):
    command_id = await create(client, robot_id=4)
    await status_consumer.process(
        [
            status_report(command_id, 4, "COMPLETED", 1),  # PENDING -> COMPLETED is not allowed
            status_report("no-such-command", 4, "ACKNOWLEDGED", 2),
            Delivery(4, "commands/status", b'{"status": "FAILED"}', 3, 1, 0),
        ]
    )
    assert await status_of(command_id) == "PENDING"
    assert status_consumer.acked == [1, 2, 3]
    assert status_consumer.stats["rejected"] == 3


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


class Robot:
    """A robot with a persistent session subscribed to its commands."""

    def __init__(self, robot_id):
        self.robot_id = robot_id
        self.received = []
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"test-robot-{robot_id}-{uuid.uuid4().hex[:6]}",
            protocol=mqtt.MQTTv5,
        )
        self.client.on_message = lambda c, u, m: self.received.append(orjson.loads(m.payload))

    def connect(self):
        self.client.connect(MQTT_HOST, 1883, clean_start=False)
        self.client.loop_start()
        self.client.subscribe(f"fleet/{self.robot_id}/commands", qos=1)
        time.sleep(0.3)

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


async def service_link():
    consumer = IngestConsumer(host=MQTT_HOST, username=f"svc-{uuid.uuid4().hex[:6]}", password="")
    task = asyncio.create_task(consumer.run())
    await asyncio.wait_for(consumer.connected.wait(), 10)
    return consumer, task


async def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@broker
@pytest.mark.asyncio
async def test_command_reaches_a_robot_through_the_broker(client, install, broadcasts):
    robot_id = 9000 + int(uuid.uuid4().int % 900)
    consumer, task = await service_link()
    robot = Robot(robot_id)
    robot.connect()
    try:
        publisher = install(consumer.client)
        command_id = await create(client, robot_id)
        await settle(publisher)
        assert await status_of(command_id) == "DISPATCHED"
        assert await wait_until(lambda: _received(robot, command_id))
    finally:
        robot.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@broker
@pytest.mark.asyncio
async def test_command_for_an_unseen_robot_waits_until_it_subscribes(client, install, broadcasts):
    robot_id = 9000 + int(uuid.uuid4().int % 900)
    consumer, task = await service_link()
    robot = Robot(robot_id)
    try:
        publisher = install(consumer.client)
        command_id = await create(client, robot_id)
        await settle(publisher)
        assert await status_of(command_id) == "PENDING"  # broker said 0x10

        robot.connect()
        assert await publisher.push_pending(robot_id) == 1
        assert await status_of(command_id) == "DISPATCHED"
        assert await wait_until(lambda: _received(robot, command_id))
    finally:
        robot.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _received(robot, command_id):
    return any(m["id"] == command_id for m in robot.received)
