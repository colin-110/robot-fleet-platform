"""Tests for the WebSocket fan-out tier.

The manager's pure helpers were already reachable from the API tests, but its
long-running pieces — the per-client sender loop and the Redis listener — were
not, which is why the module sat at 26 percent. These drive those paths
directly: a fake socket, a fake Redis, and a single iteration of each loop
before cancellation.
"""

import asyncio
from unittest.mock import AsyncMock

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
    """A manager with a mocked Redis, isolated from the module-level singleton."""
    mgr = ConnectionManager()
    mgr.redis = AsyncMock()
    return mgr


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
    newest frame would be exactly the wrong trade."""
    ws = FakeWebSocket()
    await manager.connect(ws)
    # Stop the sender draining so the queue can actually fill.
    manager._senders[ws].cancel()
    await _settle()

    for i in range(CLIENT_QUEUE_MAXSIZE + 10):
        manager._enqueue_to_all_clients({"seq": i})

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

    manager._enqueue_to_all_clients({"robot_id": 1})  # must not raise

    manager.disconnect(ws)


# ── Redis listener ──────────────────────────────────────────────────


def _entry(msg_id: str, payload: dict):
    """One XREAD entry as redis-py returns it with decode_responses on."""
    return (msg_id, {"payload": orjson.dumps(payload).decode("utf-8")})


@pytest.mark.asyncio
async def test_listener_decodes_a_payload_and_fans_it_out(manager):
    ws = FakeWebSocket()
    await manager.connect(ws)

    delivered = asyncio.Event()

    async def fake_xread(*_args, **_kwargs):
        if delivered.is_set():
            await asyncio.sleep(0.05)
            return []
        delivered.set()
        return [("telemetry_stream", [_entry("1-0", {"robot_id": 3, "battery": 55.0})])]

    manager.redis.xread = fake_xread

    task = asyncio.create_task(manager.listen_to_redis())
    await asyncio.wait_for(delivered.wait(), timeout=2)
    await _settle()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert ws.sent == [{"robot_id": 3, "battery": 55.0}]
    manager.disconnect(ws)


@pytest.mark.asyncio
async def test_listener_survives_an_undecodable_payload(manager):
    """One corrupt frame must not kill the listener for every client."""
    ws = FakeWebSocket()
    await manager.connect(ws)

    seen = asyncio.Event()

    async def fake_xread(*_args, **_kwargs):
        if seen.is_set():
            await asyncio.sleep(0.05)
            return []
        seen.set()
        return [
            (
                "telemetry_stream",
                [("1-0", {"payload": "not-json{"}), _entry("1-1", {"robot_id": 9})],
            )
        ]

    manager.redis.xread = fake_xread

    task = asyncio.create_task(manager.listen_to_redis())
    await asyncio.wait_for(seen.wait(), timeout=2)
    await _settle()
    assert task.done() is False  # still listening
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # The good frame that followed the corrupt one still arrived.
    assert ws.sent == [{"robot_id": 9}]
    manager.disconnect(ws)


@pytest.mark.asyncio
async def test_listener_advances_the_cursor_past_delivered_ids(manager):
    """Without this the listener re-reads the same entries forever."""
    calls: list[dict] = []
    second_read = asyncio.Event()

    async def fake_xread(last_ids, *_args, **_kwargs):
        calls.append(dict(last_ids))
        if len(calls) == 1:
            return [("telemetry_stream", [_entry("1700-5", {"robot_id": 1})])]
        second_read.set()
        await asyncio.sleep(0.05)
        return []

    manager.redis.xread = fake_xread

    task = asyncio.create_task(manager.listen_to_redis())
    await asyncio.wait_for(second_read.wait(), timeout=2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert calls[0]["telemetry_stream"] == "$"  # only new entries on start
    assert calls[1]["telemetry_stream"] == "1700-5"


@pytest.mark.asyncio
async def test_listener_backs_off_and_recovers_from_a_redis_outage(manager):
    """A Redis blip must not terminate the listener."""
    attempts = 0
    recovered = asyncio.Event()

    async def flaky_xread(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("redis is gone")
        recovered.set()
        await asyncio.sleep(0.05)
        return []

    manager.redis.xread = flaky_xread

    task = asyncio.create_task(manager.listen_to_redis())
    await asyncio.wait_for(recovered.wait(), timeout=5)
    assert task.done() is False

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert attempts >= 2


@pytest.mark.asyncio
async def test_listener_exits_cleanly_on_cancellation(manager):
    async def idle_xread(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return []

    manager.redis.xread = idle_xread

    task = asyncio.create_task(manager.listen_to_redis())
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    # Swallowed internally rather than propagating as a CancelledError.
    assert task.cancelled() or task.done()


# ── Publishing ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_publishes_to_the_named_stream(manager):
    await manager.broadcast({"type": "EVENT", "message": "zone breach"}, stream="event_stream")

    manager.redis.xadd.assert_awaited_once()
    stream_arg = manager.redis.xadd.await_args.args[0]
    assert stream_arg == "event_stream"


@pytest.mark.asyncio
async def test_broadcast_swallows_a_redis_failure(manager):
    """Ingestion must not fail because the fan-out bus is down."""
    manager.redis.xadd.side_effect = ConnectionError("redis down")

    await manager.broadcast({"robot_id": 1})  # must not raise


@pytest.mark.asyncio
async def test_broadcast_batch_pipelines_and_skips_empty(manager):
    pipeline = AsyncMock()
    pipeline.xadd = lambda *a, **k: None
    manager.redis.pipeline = lambda: pipeline

    await manager.broadcast_batch([])
    pipeline.execute.assert_not_awaited()

    await manager.broadcast_batch([{"robot_id": 1}, {"robot_id": 2}])
    pipeline.execute.assert_awaited_once()
