"""The direct-write paths are idempotent: a retried reading is stored once.

The worker's redelivery case lives in ``test_worker_idempotency.py``. These cover
the paths that do not go through the stream — the direct ingest endpoints and
the in-memory buffer flush — all of which share ``TelemetryRepository``.
"""

import pytest
from sqlalchemy import func, select

from app import telemetry_buffer
from app.models import Robot, Telemetry
from app.schemas import TelemetryCreate
from tests.conftest import TestSessionLocal

TS = "2026-07-22T00:00:00Z"


@pytest.fixture
def buffer_uses_test_db(monkeypatch):
    """Point ``telemetry_buffer.flush`` at the per-test engine, with an empty buffer.

    ``flush`` opens its own session from ``app.database.AsyncSessionLocal``, whose
    pooled connections belong to whichever event loop first used them. Under
    pytest each test has its own loop, so reusing that pool breaks the next test
    that touches it (the admin-bootstrap test does). The test engine uses NullPool.
    """
    monkeypatch.setattr(telemetry_buffer, "AsyncSessionLocal", TestSessionLocal)
    telemetry_buffer._pending.clear()
    yield
    telemetry_buffer._pending.clear()


async def _row_count(db) -> int:
    result = await db.execute(select(func.count()).select_from(Telemetry))
    return result.scalar_one()


async def test_retried_single_reading_is_stored_once(client, db, sample_telemetry):
    payload = {**sample_telemetry, "timestamp": TS}

    first = await client.post("/api/v1/telemetry", json=payload)
    retry = await client.post("/api/v1/telemetry", json=payload)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["message"] == "Telemetry already received"
    # The retry is answered with the original row's id.
    assert retry.json()["id"] == first.json()["id"]
    assert await _row_count(db) == 1


async def test_batch_skips_readings_that_already_exist(client, db, sample_telemetry):
    first = {**sample_telemetry, "timestamp": TS}
    second = {**sample_telemetry, "timestamp": "2026-07-22T00:00:01Z"}

    await client.post("/api/v1/telemetry", json=first)
    response = await client.post("/api/v1/telemetry/batch", json=[first, second])

    assert response.status_code == 200
    assert await _row_count(db) == 2


async def test_duplicate_inside_one_batch_is_stored_once(client, db, sample_telemetry):
    reading = {**sample_telemetry, "timestamp": TS}

    response = await client.post("/api/v1/telemetry/batch", json=[reading, reading])

    assert response.status_code == 200
    assert await _row_count(db) == 1


async def test_same_instant_for_different_robots_are_both_kept(client, db, sample_telemetry):
    one = {**sample_telemetry, "robot_id": 1, "timestamp": TS}
    two = {**sample_telemetry, "robot_id": 2, "timestamp": TS}

    await client.post("/api/v1/telemetry/batch", json=[one, two])

    assert await _row_count(db) == 2


@pytest.mark.parametrize("flushes", [1, 2])
async def test_buffer_flush_is_idempotent(db, sample_telemetry, flushes, buffer_uses_test_db):
    from datetime import datetime, timezone

    reading = TelemetryCreate(**sample_telemetry)
    ts = datetime(2026, 7, 22, tzinfo=timezone.utc)

    for _ in range(flushes):
        await telemetry_buffer.buffer([(reading, ts)])
        await telemetry_buffer.flush()

    assert await _row_count(db) == 1


async def test_buffer_flush_registers_the_robot(db, sample_telemetry, buffer_uses_test_db):
    """flush used to raise after the insert, so the roster was never updated."""
    from datetime import datetime, timezone

    reading = TelemetryCreate(**sample_telemetry)
    await telemetry_buffer.buffer([(reading, datetime(2026, 7, 22, tzinfo=timezone.utc))])

    await telemetry_buffer.flush()

    robot_ids = (await db.execute(select(Robot.id))).scalars().all()
    assert list(robot_ids) == [sample_telemetry["robot_id"]]
