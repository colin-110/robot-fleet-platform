"""robot_state holds the latest reading per robot, updated on every write."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import RobotState

T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)


def reading(minutes: float, battery: float = 80.0, robot_id: int = 1, **extra) -> dict:
    return {
        "robot_id": robot_id,
        "battery": battery,
        "temperature": 35.0,
        "speed": 1.0,
        "timestamp": (T0 + timedelta(minutes=minutes)).isoformat(),
        **extra,
    }


async def current(db, robot_id: int = 1) -> RobotState:
    db.expire_all()
    result = await db.execute(select(RobotState).where(RobotState.robot_id == robot_id))
    return result.scalar_one()


@pytest.mark.asyncio
async def test_first_reading_creates_the_state(client, db):
    await client.post("/api/v1/telemetry", json=reading(0, battery=77.0, status="ACTIVE"))

    state = await current(db)
    assert state.battery == 77.0
    assert state.status == "ACTIVE"
    assert state.battery_drain_per_min is None


@pytest.mark.asyncio
async def test_newer_reading_replaces_the_state(client, db):
    await client.post("/api/v1/telemetry", json=reading(0, battery=80.0))
    await client.post("/api/v1/telemetry", json=reading(1, battery=79.0))

    assert (await current(db)).battery == 79.0


@pytest.mark.asyncio
async def test_older_reading_does_not_move_the_state_back(client, db):
    await client.post("/api/v1/telemetry", json=reading(5, battery=70.0))
    await client.post("/api/v1/telemetry", json=reading(1, battery=99.0))  # late upload

    state = await current(db)
    assert state.battery == 70.0
    assert state.timestamp == T0 + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_batch_uses_its_newest_reading_per_robot(client, db):
    await client.post(
        "/api/v1/telemetry/batch",
        json=[
            reading(2, battery=60.0),
            reading(3, battery=58.0),
            reading(1, battery=62.0),
            reading(1, battery=40.0, robot_id=2),
        ],
    )

    assert (await current(db, 1)).battery == 58.0
    assert (await current(db, 2)).battery == 40.0


@pytest.mark.asyncio
async def test_drain_rate_is_smoothed_from_consecutive_readings(client, db):
    await client.post("/api/v1/telemetry", json=reading(0, battery=100.0))
    await client.post("/api/v1/telemetry", json=reading(10, battery=90.0))  # 1%/min
    first = (await current(db)).battery_drain_per_min

    await client.post("/api/v1/telemetry", json=reading(15, battery=80.0))  # 2%/min
    second = (await current(db)).battery_drain_per_min

    assert first == pytest.approx(1.0)
    assert second == pytest.approx(0.2 * 2.0 + 0.8 * 1.0)


@pytest.mark.asyncio
async def test_charging_resets_the_drain_rate(client, db):
    await client.post("/api/v1/telemetry", json=reading(0, battery=50.0))
    await client.post("/api/v1/telemetry", json=reading(5, battery=45.0))
    await client.post("/api/v1/telemetry", json=reading(10, battery=60.0))

    assert (await current(db)).battery_drain_per_min is None


@pytest.mark.asyncio
async def test_fleet_status_reports_runtime_from_the_state(client):
    now = datetime.now(timezone.utc)
    for minutes_ago, battery in ((10, 100.0), (0, 90.0)):
        await client.post(
            "/api/v1/telemetry",
            json={
                "robot_id": 1,
                "battery": battery,
                "temperature": 35.0,
                "speed": 1.0,
                "battery_health": 100.0,
                "timestamp": (now - timedelta(minutes=minutes_ago)).isoformat(),
            },
        )

    [robot] = (await client.get("/api/v1/robots/status")).json()

    assert robot["battery"] == 90.0
    assert robot["runtime_remaining_minutes"] == pytest.approx(90.0, abs=0.5)
