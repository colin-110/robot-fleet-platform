"""No reading is lost or stored twice.

- every reading carries its device timestamp, which identifies it;
- the Redis-stream path skips readings already queued, and fails the request
  instead of dropping readings when Redis is down.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import func, select

from app import telemetry_stream
from app.config import get_settings
from app.models import Telemetry
from app.websocket_manager import manager
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
