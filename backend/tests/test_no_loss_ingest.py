"""No reading is lost or stored twice.

Covers the three pieces that make that true together:

- every reading carries its device timestamp, which identifies it;
- the Redis-stream path skips readings already queued;
- every response tells the robot which readings are stored (``ack``), so it
  can keep and resend anything a crashed server lost.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import func, select

from app import durability, telemetry_buffer, telemetry_stream
from app.config import get_settings
from app.models import Telemetry
from app.websocket_manager import ConnectionManager, manager
from tests.conftest import TestSessionLocal

settings = get_settings()
T0 = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def reading(robot_id: int = 1, offset_s: float = 0.0) -> dict:
    return {
        "robot_id": robot_id,
        "battery": 80.0,
        "temperature": 35.0,
        "speed": 1.0,
        "timestamp": iso(T0 + timedelta(seconds=offset_s)),
    }


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


async def row_count(db) -> int:
    return (await db.execute(select(func.count()).select_from(Telemetry))).scalar_one()


@pytest.fixture
def test_sessions(monkeypatch):
    """Point background writers at the per-test session factory."""
    monkeypatch.setattr(telemetry_buffer, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(durability, "AsyncSessionLocal", TestSessionLocal)


@pytest.fixture
def viewers(monkeypatch):
    """Control how many dashboards appear to be connected."""

    def set_count(count: int):
        monkeypatch.setattr(ConnectionManager, "connection_count", property(lambda _: count))

    return set_count


# ── Identity ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reading_without_a_timestamp_is_rejected(client):
    payload = reading()
    del payload["timestamp"]

    response = await client.post("/api/v1/telemetry", json=payload)

    assert response.status_code == 422


# ── Redis stream path ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def real_redis():
    client = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
    )
    await client.delete(telemetry_stream.STREAM_KEY)
    yield client
    keys = [k async for k in client.scan_iter("telemetry:seen:*")]
    await client.delete(telemetry_stream.STREAM_KEY, *keys)
    await client.aclose()


@pytest.mark.asyncio
async def test_stream_skips_a_reading_already_queued(real_redis):
    batch = [reading(offset_s=0), reading(offset_s=1)]

    first = await telemetry_stream.enqueue(batch, redis=real_redis)
    retry = await telemetry_stream.enqueue(batch, redis=real_redis)

    assert first == batch
    assert retry == []
    assert await real_redis.xlen(telemetry_stream.STREAM_KEY) == 2


@pytest.mark.asyncio
async def test_stream_queues_only_the_new_readings_of_a_partial_retry(real_redis):
    await telemetry_stream.enqueue([reading(offset_s=0)], redis=real_redis)

    queued = await telemetry_stream.enqueue(
        [reading(offset_s=0), reading(offset_s=1)], redis=real_redis
    )

    assert queued == [reading(offset_s=1)]
    assert await real_redis.xlen(telemetry_stream.STREAM_KEY) == 2


@pytest.mark.asyncio
async def test_queued_ingest_fails_loudly_when_redis_is_down(client, monkeypatch):
    """The request must not succeed unless the readings reached the stream."""
    monkeypatch.setattr(settings, "opt_redis_buffer", True)

    async def down(*_args, **_kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(telemetry_stream, "enqueue", down)

    with pytest.raises(ConnectionError):
        await client.post("/api/v1/telemetry", json=reading())


@pytest.mark.asyncio
async def test_queued_ingest_fans_out_locally_without_the_redis_listener(client, monkeypatch):
    """With websocket_backend=direct nothing reads the stream for dashboards."""
    monkeypatch.setattr(settings, "opt_redis_buffer", True)
    monkeypatch.setattr(settings, "websocket_backend", "direct")

    async def enqueue(payloads, *_args, **_kwargs):
        return payloads

    shown = []

    async def capture(payloads, *_args, **_kwargs):
        shown.extend(payloads)

    monkeypatch.setattr(telemetry_stream, "enqueue", enqueue)
    monkeypatch.setattr(manager, "broadcast_batch", capture)

    await client.post("/api/v1/telemetry", json=reading())

    assert len(shown) == 1


# ── Acknowledgements ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_direct_write_acks_the_stored_reading(client):
    response = await client.post(
        "/api/v1/telemetry/batch", json=[reading(offset_s=0), reading(offset_s=5)]
    )

    ack = response.json()["ack"]
    assert ack["server"] == durability.BOOT_ID
    assert ack["durable_until"] == {"1": iso(T0 + timedelta(seconds=5))}


@pytest.mark.asyncio
async def test_a_duplicate_is_acked_as_stored(client):
    await client.post("/api/v1/telemetry", json=reading())
    retry = await client.post("/api/v1/telemetry", json=reading())

    assert retry.json()["ack"]["durable_until"] == {"1": iso(T0)}


@pytest.mark.asyncio
async def test_buffered_reading_is_not_acked_until_flushed(
    client, db, viewers, test_sessions, monkeypatch
):
    monkeypatch.setattr(settings, "defer_writes_without_viewers", True)
    viewers(0)

    response = await client.post("/api/v1/telemetry", json=reading())
    assert response.json()["ack"]["durable_until"] == {"1": None}

    await telemetry_buffer.flush()

    after = await client.post("/api/v1/telemetry", json=reading(robot_id=1, offset_s=1))
    assert after.json()["ack"]["durable_until"]["1"] == iso(T0)
    assert await row_count(db) == 1


@pytest.mark.asyncio
async def test_watermark_stops_short_of_an_older_buffered_reading(client, viewers, monkeypatch):
    """A resent old reading sitting in memory must not be covered by a newer stored one."""
    durability.mark_stored([(1, T0 + timedelta(seconds=10))])
    monkeypatch.setattr(settings, "defer_writes_without_viewers", True)
    viewers(0)

    response = await client.post("/api/v1/telemetry", json=reading(offset_s=5))

    expected = T0 + timedelta(seconds=5) - timedelta(microseconds=1)
    assert response.json()["ack"]["durable_until"] == {"1": iso(expected)}


@pytest.mark.asyncio
async def test_direct_write_also_stores_what_was_buffered(client, db, viewers, monkeypatch):
    """When a viewer connects, older buffered readings go out in the same transaction."""
    monkeypatch.setattr(settings, "defer_writes_without_viewers", True)
    viewers(0)
    await client.post("/api/v1/telemetry/batch", json=[reading(offset_s=0), reading(offset_s=1)])

    viewers(1)
    response = await client.post("/api/v1/telemetry", json=reading(offset_s=2))

    assert await row_count(db) == 3
    assert telemetry_buffer._pending == []
    assert response.json()["ack"]["durable_until"] == {"1": iso(T0 + timedelta(seconds=2))}


@pytest.mark.asyncio
async def test_buffered_rows_survive_a_failed_direct_write(client, viewers, monkeypatch):
    monkeypatch.setattr(settings, "defer_writes_without_viewers", True)
    viewers(0)
    await client.post("/api/v1/telemetry", json=reading(offset_s=0))

    async def broken(*_args, **_kwargs):
        raise ConnectionError("database unavailable")

    from app.services import telemetry_service

    monkeypatch.setattr(telemetry_service, "persist", broken)
    viewers(1)
    with pytest.raises(ConnectionError):
        await client.post("/api/v1/telemetry", json=reading(offset_s=1))

    assert len(telemetry_buffer._pending) == 1


@pytest.mark.asyncio
async def test_watermarks_are_seeded_from_the_roster(client, test_sessions):
    await client.post("/api/v1/telemetry", json=reading(robot_id=4, offset_s=7))
    durability.reset()

    await durability.load()

    assert durability.durable_until(4) == T0 + timedelta(seconds=7)
