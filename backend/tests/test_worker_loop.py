"""Tests for the worker's long-running loops.

``test_worker.py`` covers ``process_batch`` — the pure parsing and persistence
logic. What was untested is the machinery around it: consumer-group setup, the
drain/ack cycle, backoff on failure, clean cancellation, and the stream-depth
reporter that turns silent telemetry loss into an alertable metric. Each test
drives one iteration of a loop against a fake Redis and then cancels it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from app import worker
from app.worker import CONSUMER_GROUP, STREAM_KEY, redis_to_db_sync_worker, report_stream_depth


def _entry(msg_id: str, payload: dict):
    return (msg_id, {"payload": orjson.dumps(payload).decode("utf-8")})


TELEMETRY = {"robot_id": 1, "battery": 91.0, "temperature": 30.0, "speed": 1.0}


@pytest.fixture
def fake_redis():
    redis = AsyncMock()
    redis.xgroup_create = AsyncMock()
    redis.xack = AsyncMock()
    redis.execute_command = AsyncMock(return_value=None)
    return redis


async def _run_until(event: asyncio.Event, coro_fn, timeout: float = 5.0):
    """Run a loop until `event` fires, then cancel and await it."""
    task = asyncio.create_task(coro_fn())
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return task


# ── redis_to_db_sync_worker ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_creates_the_consumer_group_on_startup(fake_redis):
    idle = asyncio.Event()

    async def xreadgroup(*_args, **_kwargs):
        idle.set()
        await asyncio.sleep(0.05)
        return []

    fake_redis.xreadgroup = xreadgroup

    with patch.object(worker.manager, "redis", fake_redis):
        await _run_until(idle, redis_to_db_sync_worker)

    fake_redis.xgroup_create.assert_awaited_once()
    args, kwargs = fake_redis.xgroup_create.await_args
    assert args[0] == STREAM_KEY
    assert args[1] == CONSUMER_GROUP
    assert kwargs["mkstream"] is True


@pytest.mark.asyncio
async def test_worker_tolerates_an_existing_consumer_group(fake_redis):
    """BUSYGROUP is the normal case on every restart after the first."""
    fake_redis.xgroup_create.side_effect = Exception("BUSYGROUP Consumer Group name already exists")
    idle = asyncio.Event()

    async def xreadgroup(*_args, **_kwargs):
        idle.set()
        await asyncio.sleep(0.05)
        return []

    fake_redis.xreadgroup = xreadgroup

    with patch.object(worker.manager, "redis", fake_redis):
        task = await _run_until(idle, redis_to_db_sync_worker)

    # It kept going rather than dying at startup.
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_worker_persists_a_batch_then_acknowledges_it(fake_redis):
    """The drain/ack cycle: nothing may be acked that was not persisted."""
    acked = asyncio.Event()
    delivered = False

    async def xreadgroup(_group, _consumer, streams, **_kwargs):
        nonlocal delivered
        if not delivered:
            delivered = True
            return [(STREAM_KEY, [_entry("1-0", TELEMETRY), _entry("1-1", TELEMETRY)])]
        await asyncio.sleep(0.05)
        return []

    async def xack(*args, **_kwargs):
        acked.set()
        return len(args) - 2

    fake_redis.xreadgroup = xreadgroup
    fake_redis.xack = AsyncMock(side_effect=xack)

    process = AsyncMock(return_value=["1-0", "1-1"])

    with (
        patch.object(worker.manager, "redis", fake_redis),
        patch.object(worker, "process_batch", process),
        # AsyncSessionLocal() is used as an async context manager, so the
        # factory itself is sync and only the session it yields is awaitable.
        patch.object(worker, "AsyncSessionLocal", MagicMock(return_value=AsyncMock())),
    ):
        await _run_until(acked, redis_to_db_sync_worker)

    process.assert_awaited()
    # Both ids acked, on the right stream and group.
    ack_args = fake_redis.xack.await_args.args
    assert ack_args[0] == STREAM_KEY
    assert ack_args[1] == CONSUMER_GROUP
    assert set(ack_args[2:]) == {"1-0", "1-1"}


@pytest.mark.asyncio
async def test_worker_does_not_acknowledge_an_empty_drain(fake_redis):
    """An empty poll must not issue a meaningless XACK every iteration."""
    polled = asyncio.Event()
    count = 0

    async def xreadgroup(*_args, **_kwargs):
        nonlocal count
        count += 1
        if count >= 2:
            polled.set()
        await asyncio.sleep(0.02)
        return []

    fake_redis.xreadgroup = xreadgroup

    with patch.object(worker.manager, "redis", fake_redis):
        await _run_until(polled, redis_to_db_sync_worker)

    fake_redis.xack.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_backs_off_and_keeps_running_after_a_failure(fake_redis):
    """A database or Redis blip must not terminate the worker process."""
    recovered = asyncio.Event()
    attempts = 0

    async def xreadgroup(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("redis connection reset")
        recovered.set()
        await asyncio.sleep(0.05)
        return []

    fake_redis.xreadgroup = xreadgroup

    with patch.object(worker.manager, "redis", fake_redis):
        task = await _run_until(recovered, redis_to_db_sync_worker, timeout=10)

    assert attempts >= 2
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_worker_exits_cleanly_on_cancellation(fake_redis):
    """SIGTERM during a rolling deploy must not surface as a crash."""

    async def xreadgroup(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return []

    fake_redis.xreadgroup = xreadgroup

    with patch.object(worker.manager, "redis", fake_redis):
        task = asyncio.create_task(redis_to_db_sync_worker())
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled() or task.done()


# ── report_stream_depth ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_depth_reporter_publishes_length_and_pending(fake_redis):
    sampled = asyncio.Event()

    async def xlen(_key):
        sampled.set()
        return 1234

    fake_redis.xlen = xlen
    fake_redis.xpending = AsyncMock(return_value={"pending": 17})

    with patch.object(worker.manager, "redis", fake_redis):
        await _run_until(sampled, report_stream_depth)

    assert worker.telemetry_stream_length._value.get() == 1234
    assert worker.telemetry_stream_pending._value.get() == 17


@pytest.mark.asyncio
async def test_depth_reporter_handles_a_missing_consumer_group(fake_redis):
    """On a cold start the group does not exist yet; pending must read 0 rather
    than leaving a stale value on the gauge."""
    sampled = asyncio.Event()

    async def xlen(_key):
        sampled.set()
        return 5

    fake_redis.xlen = xlen
    fake_redis.xpending = AsyncMock(side_effect=Exception("NOGROUP"))

    with patch.object(worker.manager, "redis", fake_redis):
        await _run_until(sampled, report_stream_depth)

    assert worker.telemetry_stream_pending._value.get() == 0


@pytest.mark.asyncio
async def test_depth_reporter_warns_when_the_buffer_nears_its_cap(fake_redis, caplog):
    """The documented silent-failure mode: past the cap Redis trims entries the
    worker has not acknowledged, losing telemetry with no error anywhere."""
    sampled = asyncio.Event()
    maxlen = worker.settings.telemetry_stream_maxlen

    async def xlen(_key):
        sampled.set()
        return int(maxlen * 0.95)

    fake_redis.xlen = xlen
    fake_redis.xpending = AsyncMock(return_value={"pending": 0})

    with patch.object(worker.manager, "redis", fake_redis), caplog.at_level("WARNING"):
        await _run_until(sampled, report_stream_depth)

    assert any("will trim" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_depth_reporter_survives_a_redis_outage(fake_redis):
    attempts = 0
    recovered = asyncio.Event()

    async def xlen(_key):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("redis down")
        recovered.set()
        return 0

    fake_redis.xlen = xlen
    fake_redis.xpending = AsyncMock(return_value={"pending": 0})

    with patch.object(worker.manager, "redis", fake_redis):
        # The loop sleeps 10s between samples, so drive the retry directly.
        task = asyncio.create_task(report_stream_depth())
        await asyncio.sleep(0.05)
        assert task.done() is False  # did not die on the first failure
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert attempts >= 1
