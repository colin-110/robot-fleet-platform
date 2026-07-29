"""Coverage for the buffered ingest path — the one that runs in production.

``conftest.py`` pins ``opt_redis_buffer = False`` for the whole suite so that
route tests can assert on synchronous database writes, and CI sets the same
flag. The consequence was that every test exercised the *baseline* branch —
the deliberately slow implementation kept only as a benchmark comparison —
while the default production path, and the centrepiece of the architecture,
was never executed at all.

These tests flip the flag and assert the property that makes the design worth
having: the request performs no database work, it only hands the reading to
Redis.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models import Telemetry

settings = get_settings()


@pytest.fixture
def buffered(monkeypatch):
    """Turn the Redis buffer on for one test."""
    monkeypatch.setattr(settings, "opt_redis_buffer", True)


@pytest.fixture
def captured_broadcasts(monkeypatch):
    """Record what would have been XADDed, without needing a live Redis."""
    from app.websocket_manager import manager

    sent = []

    async def fake_broadcast(payload, *_args, **_kwargs):
        sent.append(payload)

    async def fake_broadcast_batch(payloads, *_args, **_kwargs):
        sent.extend(payloads)

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    monkeypatch.setattr(manager, "broadcast_batch", fake_broadcast_batch)
    return sent


async def _row_count(db) -> int:
    result = await db.execute(select(func.count()).select_from(Telemetry))
    return result.scalar_one()


# ── Single reading ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buffered_ingest_writes_nothing_to_the_database(
    client, db, sample_telemetry, buffered, captured_broadcasts
):
    """The whole point of the buffer: no DB round trip on the request path."""
    response = await client.post("/api/v1/telemetry", json=sample_telemetry)

    assert response.status_code == 200
    assert await _row_count(db) == 0
    assert response.json()["message"] == "Telemetry queued in Redis"


@pytest.mark.asyncio
async def test_buffered_ingest_hands_the_reading_to_redis(
    client, sample_telemetry, buffered, captured_broadcasts
):
    await client.post("/api/v1/telemetry", json=sample_telemetry)

    assert len(captured_broadcasts) == 1
    payload = captured_broadcasts[0]
    assert payload["robot_id"] == sample_telemetry["robot_id"]
    assert payload["battery"] == sample_telemetry["battery"]
    # The worker keys inserts off this, so it must survive the hop.
    assert payload["timestamp"] is not None


@pytest.mark.asyncio
async def test_buffered_ingest_preserves_a_device_timestamp(
    client, sample_telemetry, buffered, captured_broadcasts
):
    """Store-and-forward: a backdated reading keeps its own time.

    Only ever verified on the direct path before, which meant the branch that
    actually ships could have collapsed the timeline unnoticed.
    """
    reported = datetime.now(timezone.utc) - timedelta(minutes=42)
    payload = {**sample_telemetry, "timestamp": reported.isoformat().replace("+00:00", "Z")}

    await client.post("/api/v1/telemetry", json=payload)

    sent = datetime.fromisoformat(captured_broadcasts[0]["timestamp"].replace("Z", "+00:00"))
    assert abs((sent - reported).total_seconds()) < 1


@pytest.mark.asyncio
async def test_buffered_ingest_clamps_a_future_timestamp(
    client, sample_telemetry, buffered, captured_broadcasts
):
    """A device with a fast clock must not sit permanently at 'most recent'."""
    far_future = datetime.now(timezone.utc) + timedelta(hours=6)
    payload = {**sample_telemetry, "timestamp": far_future.isoformat().replace("+00:00", "Z")}

    await client.post("/api/v1/telemetry", json=payload)

    sent = datetime.fromisoformat(captured_broadcasts[0]["timestamp"].replace("Z", "+00:00"))
    assert sent < far_future - timedelta(minutes=1)


# ── Batch ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buffered_batch_writes_nothing_to_the_database(
    client, db, sample_telemetry, buffered, captured_broadcasts
):
    batch = [
        {**sample_telemetry, "robot_id": 1},
        {**sample_telemetry, "robot_id": 2},
        {**sample_telemetry, "robot_id": 3},
    ]

    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 200
    assert await _row_count(db) == 0
    assert len(captured_broadcasts) == 3


@pytest.mark.asyncio
async def test_buffered_batch_resolves_each_reading_separately(
    client, sample_telemetry, buffered, captured_broadcasts
):
    """Stamping a whole batch with one time flattens the per-minute trend
    buckets and zeroes the span the drain-rate estimate divides by."""
    first = datetime.now(timezone.utc) - timedelta(minutes=10)
    second = datetime.now(timezone.utc) - timedelta(minutes=5)
    batch = [
        {**sample_telemetry, "robot_id": 1, "timestamp": first.isoformat().replace("+00:00", "Z")},
        {**sample_telemetry, "robot_id": 1, "timestamp": second.isoformat().replace("+00:00", "Z")},
    ]

    await client.post("/api/v1/telemetry/batch", json=batch)

    stamps = [
        datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")) for p in captured_broadcasts
    ]
    assert stamps[0] != stamps[1]
    assert abs((stamps[1] - stamps[0]).total_seconds() - 300) < 2


# ── Both paths agree ────────────────────────────────────────────────


@pytest.mark.parametrize("use_buffer", [True, False])
@pytest.mark.asyncio
async def test_both_ingest_paths_accept_the_same_payload(
    client, sample_telemetry, monkeypatch, captured_broadcasts, use_buffer
):
    """The flag selects an implementation, not an API contract."""
    monkeypatch.setattr(settings, "opt_redis_buffer", use_buffer)

    response = await client.post("/api/v1/telemetry", json=sample_telemetry)

    assert response.status_code == 200
    assert len(captured_broadcasts) == 1
    assert captured_broadcasts[0]["robot_id"] == sample_telemetry["robot_id"]
