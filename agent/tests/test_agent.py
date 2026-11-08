"""The agent against a real broker (``MQTT_TEST_HOST``, anonymous Mosquitto).

``FakeServer`` stands in for the FleetOps API: it records every envelope and
confirms contiguous progress on ``fleet/<id>/stored`` the way the real
consumer does. Each test uses its own robot id, because retained messages
outlive the test.
"""

import json
import os
import random
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time

import paho.mqtt.client as mqtt
import pytest
from fleetops_agent import wire
from fleetops_agent.agent import Agent, _runs

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


def wait_until(predicate, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class FakeServer:
    def __init__(self, robot_id: int, *, drop_first: int = 0, confirm: bool = True):
        self.robot_id = robot_id
        self.drop_first = drop_first  # envelopes to "lose", like a crashed broker
        self.confirm = confirm
        self.envelopes: list[wire.Envelope] = []
        self.readings: dict[int, dict] = {}  # by sequence number
        self.statuses: list[dict] = []
        self.presence: list[dict] = []
        self.mark: tuple[str, int] | None = None
        self.lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
        self.client.on_message = self._on_message
        self.client.on_connect = lambda c, *a: c.subscribe(f"fleet/{robot_id}/#", qos=1)
        self.client.connect(MQTT_HOST, 1883)
        self.client.loop_start()
        time.sleep(0.2)

    def _on_message(self, client, userdata, message):
        channel = message.topic.split("/", 2)[2]
        if channel == "presence":
            self.presence.append(json.loads(message.payload))
            return
        if channel not in wire.CHANNELS:
            return
        assert message.properties.ContentType == wire.CONTENT_TYPE
        envelope = wire.decode_envelope(channel, message.payload)
        with self.lock:
            if self.drop_first:
                self.drop_first -= 1
                return
            self.envelopes.append(envelope)
            for seq, packed in enumerate(envelope.items, envelope.seq):
                values = wire.unpack_item(channel, packed)
                if channel == "telemetry":
                    self.readings[seq] = values
                elif channel == "commands/status":
                    self.statuses.append(values)
            stream, seq = (
                self.mark
                if self.mark and self.mark[0] == envelope.stream
                else (
                    envelope.stream,
                    0,
                )
            )
            seq = max(seq, envelope.floor - 1)
            if envelope.seq <= seq + 1:
                seq = max(seq, envelope.last_seq)
            self.mark = (stream, seq)
            if self.confirm:
                client.publish(
                    f"fleet/{self.robot_id}/stored",
                    wire.encode_stored(stream, seq),
                    qos=0,
                    retain=True,
                )

    def push_command(self, command: dict) -> None:
        self.client.publish(
            f"fleet/{self.robot_id}/commands", json.dumps(command), qos=1
        ).wait_for_publish(5)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


@pytest.fixture
def robot_id():
    return random.randint(100_000, 999_999)


@pytest.fixture
def outbox_path(tmp_path):
    return str(tmp_path / "outbox.db")


def make_agent(robot_id, outbox_path, **kwargs):
    kwargs = {"flush_interval": 0.1, **kwargs}
    agent = Agent(robot_id, "key", MQTT_HOST, outbox_path=outbox_path, **kwargs)
    agent.start()
    assert wait_until(lambda: agent.connected)
    return agent


def reading(i: int) -> dict:
    return {"battery": 90 - i * 0.1, "temperature": 40.0, "speed": 1.0, "x": i, "y": 0.0}


def test_runs_split_on_channel_gaps_and_size():
    rows = [(1, "telemetry", {}), (2, "telemetry", {}), (3, "events", {}), (5, "events", {})]
    rows += [(6, "events", {}), (7, "events", {})]
    assert [[seq for seq, _, _ in run] for run in _runs(rows, limit=2)] == [
        [1, 2],
        [3],
        [5, 6],
        [7],
    ]


@broker
def test_readings_are_sent_confirmed_and_forgotten(robot_id, outbox_path):
    server = FakeServer(robot_id)
    agent = make_agent(robot_id, outbox_path)
    try:
        for i in range(50):
            agent.send_telemetry(reading(i))
        assert agent.flush()
        assert sorted(server.readings) == list(range(1, 51))
        assert server.readings[50]["x"] == 49
        # Batched: far fewer messages than readings.
        assert len(server.envelopes) < 10
    finally:
        agent.stop()
        server.close()


@broker
def test_nothing_is_forgotten_without_a_confirmation(robot_id, outbox_path):
    server = FakeServer(robot_id, confirm=False)
    agent = make_agent(robot_id, outbox_path)
    try:
        for i in range(5):
            agent.send_telemetry(reading(i))
        assert wait_until(lambda: len(server.readings) == 5)
        time.sleep(0.3)
        # The broker has them; the robot still keeps them.
        assert agent.pending() == 5
    finally:
        agent.stop()
        server.close()


@broker
def test_items_lost_after_the_broker_accepted_them_are_resent(robot_id, outbox_path):
    # The broker acknowledges the first envelopes and then "loses" them.
    server = FakeServer(robot_id, drop_first=2)
    agent = make_agent(robot_id, outbox_path, resend_after=0.5, batch_items=5)
    try:
        for i in range(10):
            agent.send_telemetry(reading(i))
        assert agent.flush()
        assert sorted(server.readings) == list(range(1, 11))
        assert agent.stats["resends"] >= 1
    finally:
        agent.stop()
        server.close()


@broker
def test_a_killed_robot_resends_what_it_held(robot_id, outbox_path):
    # No server while the first process runs, so nothing is ever confirmed.
    script = textwrap.dedent(
        f"""
        import os, signal, time
        from fleetops_agent.agent import Agent
        agent = Agent({robot_id}, "key", {MQTT_HOST!r}, outbox_path={outbox_path!r})
        agent.start()
        for i in range(30):
            agent.send_telemetry({{"battery": 50.0, "temperature": 30.0, "speed": 0.0, "x": i}})
        time.sleep(1.5)
        print("sent", flush=True)
        os.kill(os.getpid(), signal.SIGKILL)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    assert "sent" in result.stdout

    server = FakeServer(robot_id)
    agent = make_agent(robot_id, outbox_path)
    try:
        assert agent.flush()
        assert sorted(server.readings) == list(range(1, 31))
    finally:
        agent.stop()
        server.close()


@broker
def test_the_window_bounds_what_is_in_flight(robot_id, outbox_path):
    server = FakeServer(robot_id, confirm=False)
    agent = make_agent(robot_id, outbox_path, window=20, batch_items=10)
    try:
        for i in range(100):
            agent.send_telemetry(reading(i))
        assert wait_until(lambda: len(server.readings) == 20)
        time.sleep(0.5)
        assert len(server.readings) == 20
    finally:
        agent.stop()
        server.close()


@broker
def test_commands_arrive_once_and_reports_go_back(robot_id, outbox_path):
    server = FakeServer(robot_id)
    agent = make_agent(robot_id, outbox_path)
    try:
        command = {"id": "c-1", "command_type": "RETURN_TO_BASE", "payload": None}
        server.push_command(command)
        server.push_command(command)
        assert wait_until(lambda: agent.stats["repeated_commands"] == 1)
        assert agent.take_commands() == [command]
        assert agent.take_commands() == []

        agent.report_command("c-1", "COMPLETED", result={"docked": True})
        assert agent.flush()
        assert server.statuses == [{"id": "c-1", "status": "COMPLETED", "result": {"docked": True}}]
    finally:
        agent.stop()
        server.close()


@broker
def test_presence_online_then_offline_on_stop(robot_id, outbox_path):
    server = FakeServer(robot_id)
    agent = make_agent(robot_id, outbox_path)
    assert wait_until(lambda: server.presence and server.presence[-1]["online"])
    agent.stop()
    try:
        assert wait_until(lambda: server.presence[-1]["online"] is False)
        assert server.presence[0]["conn"] == server.presence[-1]["conn"]
    finally:
        server.close()
