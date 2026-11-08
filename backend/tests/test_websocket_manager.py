"""Tests for the WebSocket fan-out tier.

The manager's pure helpers were already reachable from the API tests, but its
long-running piece - the per-client sender loop - was not. These drive it
directly with a fake socket, plus the in-process and broker-relay fan-out.
"""

import asyncio

import orjson
import pytest

from app.websocket_manager import CLIENT_QUEUE_MAXSIZE, ConnectionManager


class FakeWebSocket:
    """Stands in for a Starlette WebSocket without a network or an event loop."""

    def __init__(self, fail_on_send: Exception | None = None):
        self.accepted = False
        self.sent: list[dict] = []
        self._fail_on_send = fail_on_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        if self._fail_on_send is not None:
            raise self._fail_on_send
        self.sent.append(data)


@pytest.fixture
def manager():
    """A manager isolated from the module-level singleton."""
    return ConnectionManager()


async def _settle():
    """Let queued sender tasks run without wall-clock sleeping."""
    for _ in range(5):
        await asyncio.sleep(0)


# ── Connection lifecycle ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_accepts_and_starts_a_sender(manager):
    ws = FakeWebSocket()

    await manager.connect(ws)

    assert ws.accepted is True
    assert manager.connection_count == 1
    assert ws in manager._queues
    assert manager._senders[ws].done() is False

    manager.disconnect(ws)


@pytest.mark.asyncio
async def test_disconnect_cancels_the_sender_task(manager):
    """A leaked sender task would keep a dead connection's coroutine alive."""
    ws = FakeWebSocket()
    await manager.connect(ws)
    sender = manager._senders[ws]

    manager.disconnect(ws)
    await _settle()

    assert manager.connection_count == 0
    assert ws not in manager._queues
    assert sender.cancelled() or sender.done()


@pytest.mark.asyncio
async def test_disconnect_is_idempotent(manager):
    """The sender loop and the route handler can both report the same close."""
    ws = FakeWebSocket()
    await manager.connect(ws)

    manager.disconnect(ws)
    manager.disconnect(ws)  # must not raise or drive the count negative

    assert manager.connection_count == 0


# ── Sender loop ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sender_loop_delivers_queued_frames(manager):
    ws = FakeWebSocket()
    await manager.connect(ws)

    manager._fan_out({"robot_id": 1, "battery": 90.0})
    await _settle()

    assert ws.sent == [{"robot_id": 1, "battery": 90.0}]
    manager.disconnect(ws)


@pytest.mark.asyncio
async def test_sender_loop_drops_a_client_whose_socket_errors(manager):
    """A send failure must retire the connection, not spin the loop forever."""
    ws = FakeWebSocket(fail_on_send=RuntimeError("socket closed"))
    await manager.connect(ws)

    manager._fan_out({"robot_id": 1, "battery": 90.0})
    await _settle()

    assert manager.connection_count == 0
    assert ws not in manager._queues


# ── Bounded fan-out ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fan_out_reaches_every_connected_client(manager):
    sockets = [FakeWebSocket() for _ in range(3)]
    for ws in sockets:
        await manager.connect(ws)

    manager._fan_out({"robot_id": 7})
    await _settle()

    assert all(ws.sent == [{"robot_id": 7}] for ws in sockets)
    for ws in sockets:
        manager.disconnect(ws)


@pytest.mark.asyncio
async def test_overflow_drops_the_oldest_frame_not_the_newest(manager):
    """The design claim: a slow consumer sheds stale telemetry, keeping the
    freshest. Position data from three seconds ago is noise, so dropping the
    newest frame would be the wrong trade."""
    ws = FakeWebSocket()
    await manager.connect(ws)
    # Stop the sender draining so the queue can actually fill.
    manager._senders[ws].cancel()
    await _settle()

    for i in range(CLIENT_QUEUE_MAXSIZE + 10):
        manager._fan_out({"seq": i})

    queue = manager._queues[ws]
    assert queue.qsize() == CLIENT_QUEUE_MAXSIZE

    drained = [queue.get_nowait()["seq"] for _ in range(queue.qsize())]
    # The 10 oldest were evicted; the newest frame is still present.
    assert drained[0] == 10
    assert drained[-1] == CLIENT_QUEUE_MAXSIZE + 9

    manager.disconnect(ws)


@pytest.mark.asyncio
async def test_fan_out_skips_a_client_with_no_queue(manager):
    """A socket mid-teardown is in the list but has no queue; that must not
    raise inside the broadcast loop and stall every other client."""
    ws = FakeWebSocket()
    await manager.connect(ws)
    manager._queues.pop(ws)

    manager._fan_out({"robot_id": 1})  # must not raise

    manager.disconnect(ws)


# ── Broadcasting (websocket_backend=direct) ─────────────────────────


@pytest.mark.asyncio
async def test_broadcast_fans_out_in_process(manager):
    ws = FakeWebSocket()
    await manager.connect(ws)
    await manager.broadcast({"type": "EVENT", "message": "zone breach"})
    await manager.broadcast_batch([{"robot_id": 1}, {"robot_id": 2}])
    await manager.broadcast_batch([])
    await _settle()
    assert ws.sent == [
        {"type": "EVENT", "message": "zone breach"},
        {"robot_id": 1},
        {"robot_id": 2},
    ]
    manager.disconnect(ws)


# ── Relay through the broker (websocket_backend=mqtt) ───────────────


@pytest.fixture
def mqtt_mode(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "websocket_backend", "mqtt")


@pytest.mark.asyncio
async def test_relay_sends_one_message_per_batch(manager, mqtt_mode):
    sent = []
    manager.relay = sent.append
    await manager.broadcast_batch([{"robot_id": 1}, {"robot_id": 2}])
    await manager.broadcast({"type": "EVENT"})
    assert [orjson.loads(m) for m in sent] == [
        [{"robot_id": 1}, {"robot_id": 2}],
        [{"type": "EVENT"}],
    ]


@pytest.mark.asyncio
async def test_relayed_messages_reach_local_sockets(manager):
    ws = FakeWebSocket()
    await manager.connect(ws)
    manager.fan_out_relayed(orjson.dumps([{"robot_id": 1}, {"robot_id": 2}]))
    manager.fan_out_relayed(b"not json")
    await _settle()
    assert ws.sent == [{"robot_id": 1}, {"robot_id": 2}]
    manager.disconnect(ws)


@pytest.mark.asyncio
async def test_without_a_broker_connection_local_clients_still_get_updates(manager, mqtt_mode):
    ws = FakeWebSocket()
    await manager.connect(ws)
    await manager.broadcast({"robot_id": 3})
    await _settle()
    assert ws.sent == [{"robot_id": 3}]
    manager.disconnect(ws)


# ── Subscriptions and watching ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_subscription_filters_telemetry_but_not_alarms(manager):
    ws, other = FakeWebSocket(), FakeWebSocket()
    await manager.connect(ws)
    await manager.connect(other)
    manager.handle_client_message(ws, orjson.dumps({"type": "subscribe", "robots": [2]}).decode())
    for robot_id in (1, 2):
        await manager.broadcast({"robot_id": robot_id, "battery": 50.0})
    await manager.broadcast({"type": "ALARM", "alarm": {"robot_id": 1}})
    await _settle()
    assert [m.get("robot_id") for m in ws.sent] == [2, None]
    assert len(other.sent) == 3
    # null: everything again.
    manager.handle_client_message(ws, '{"type": "subscribe", "robots": null}')
    await manager.broadcast({"robot_id": 1, "battery": 49.0})
    await _settle()
    assert ws.sent[-1]["robot_id"] == 1


@pytest.mark.asyncio
async def test_watched_robots_are_collected_and_forgotten(manager):
    a, b = FakeWebSocket(), FakeWebSocket()
    await manager.connect(a)
    await manager.connect(b)
    manager.handle_client_message(a, '{"type": "watch", "robots": [3, 4]}')
    manager.handle_client_message(b, '{"type": "watch", "robots": [4, 5, true, "x"]}')
    assert manager.watched() == {3, 4, 5}
    manager.disconnect(a)
    assert manager.watched() == {4, 5}


@pytest.mark.asyncio
async def test_bad_control_messages_are_ignored(manager):
    ws = FakeWebSocket()
    await manager.connect(ws)
    for text in ("not json", "[]", '{"type": "subscribe", "robots": "all"}', '{"type": "x"}'):
        manager.handle_client_message(ws, text)
    await manager.broadcast({"robot_id": 9, "battery": 1.0})
    await _settle()
    assert len(ws.sent) == 1
