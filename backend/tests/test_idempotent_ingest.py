"""Telemetry writes are idempotent.

A reading is identified by (robot_id, timestamp). Retried uploads, duplicate
readings inside a batch, and worker batches replayed after a crash must all
leave exactly one row per reading.
"""

from datetime import datetime, timedelta, timezone

import orjson
import pytest
from sqlalchemy import func, select

from app.models import Robot, Telemetry
from app.schemas import TelemetryCreate
from app.websocket_manager import manager
from app.worker import process_batch
from tests.conftest import TestSessionLocal

T0 = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def reading(robot_id: int = 1, offset_s: float = 0.0, battery: float = 80.0) -> dict:
    return {
        "robot_id": robot_id,
        "battery": battery,
        "temperature": 35.0,
        "speed": 1.0,
        "timestamp": (T0 + timedelta(seconds=offset_s)).isoformat(),
    }


async def row_count(db) -> int:
    return (await db.execute(select(func.count()).select_from(Telemetry))).scalar_one()


@pytest.fixture
def broadcasts(monkeypatch):
    sent: list[dict] = []

    async def capture(payloads, *_args, **_kwargs):
        sent.extend(payloads)

    monkeypatch.setattr(manager, "broadcast_batch", capture)
    return sent


@pytest.mark.asyncio
async def test_retried_reading_is_stored_once(client, db):
    first = await client.post("/api/v1/telemetry", json=reading())
    retry = await client.post("/api/v1/telemetry", json=reading())

    assert first.json()["message"] == "Telemetry received"
    assert retry.status_code == 200
    assert retry.json()["message"] == "Telemetry already received"
    assert await row_count(db) == 1


@pytest.mark.asyncio
async def test_retried_batch_is_stored_once(client, db):
    batch = [reading(offset_s=i) for i in range(5)]

    await client.post("/api/v1/telemetry/batch", json=batch)
    retry = await client.post("/api/v1/telemetry/batch", json=batch)

    assert retry.json()["message"] == "0 telemetry readings received, 5 already stored"
    assert await row_count(db) == 5


@pytest.mark.asyncio
async def test_duplicates_inside_one_batch_are_stored_once(client, db):
    batch = [reading(offset_s=0), reading(offset_s=0), reading(offset_s=1)]

    response = await client.post("/api/v1/telemetry/batch", json=batch)

    assert response.status_code == 200
    assert await row_count(db) == 2


@pytest.mark.asyncio
async def test_same_time_from_different_robots_is_not_a_duplicate(client, db):
    await client.post("/api/v1/telemetry/batch", json=[reading(robot_id=1), reading(robot_id=2)])

    assert await row_count(db) == 2


@pytest.mark.asyncio
async def test_retry_is_not_broadcast_twice(client, broadcasts):
    await client.post("/api/v1/telemetry", json=reading())
    await client.post("/api/v1/telemetry", json=reading())

    assert len(broadcasts) == 1


@pytest.mark.asyncio
async def test_first_write_wins(client, db):
    """A retry carrying different values does not overwrite the stored row."""
    await client.post("/api/v1/telemetry", json=reading(battery=80.0))
    await client.post("/api/v1/telemetry", json=reading(battery=10.0))

    stored = (await db.execute(select(Telemetry.battery))).scalar_one()
    assert stored == 80.0


@pytest.mark.asyncio
async def test_replayed_worker_batch_is_stored_once(db):
    """The worker commits before XACK; a crash in between replays the batch."""
    messages = [
        (f"1-{i}", {"payload": orjson.dumps(reading(offset_s=i)).decode()}) for i in range(3)
    ]

    for _ in range(2):
        async with TestSessionLocal() as session:
            ids = await process_batch(session, messages)
        assert ids == ["1-0", "1-1", "1-2"]

    assert await row_count(db) == 3


@pytest.mark.asyncio
async def test_worker_skips_invalid_payloads_but_acks_them(db):
    messages = [
        ("1-0", {"payload": orjson.dumps({"robot_id": "x", "battery": 1}).decode()}),
        ("1-1", {"payload": orjson.dumps(reading()).decode()}),
    ]

    async with TestSessionLocal() as session:
        ids = await process_batch(session, messages)

    assert ids == ["1-0", "1-1"]
    assert await row_count(db) == 1


@pytest.mark.asyncio
async def test_write_registers_the_robot_in_the_same_transaction(client, db):
    await client.post("/api/v1/telemetry/batch", json=[reading(offset_s=0), reading(offset_s=30)])

    robot = (await db.execute(select(Robot).where(Robot.id == 1))).scalar_one()
    assert robot.last_seen == T0 + timedelta(seconds=30)
