"""The rollup job against the database: minutes, hours, late data, restarts."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import TelemetryHour, TelemetryMinute
from app.schemas import TelemetryCreate
from app.services.telemetry_service import store_readings
from app.storage import job
from app.storage.job import RollupJob
from tests.conftest import TestSessionLocal

# A fixed hour, so buckets are predictable.
HOUR = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)


def reading(seconds, battery, rate=None, robot_id=1):
    return TelemetryCreate(
        robot_id=robot_id,
        battery=battery,
        battery_rate=rate,
        temperature=40.0,
        speed=1.0,
        vx=1.0,
        vy=0.0,
        timestamp=HOUR + timedelta(seconds=seconds),
    )


async def minutes(robot_id=1):
    async with TestSessionLocal() as db:
        rows = await db.execute(
            select(TelemetryMinute)
            .where(TelemetryMinute.robot_id == robot_id)
            .order_by(TelemetryMinute.bucket)
        )
        return list(rows.scalars())


@pytest.fixture(autouse=True)
def no_dirty():
    job._dirty.clear()
    yield
    job._dirty.clear()


async def test_minutes_and_the_hour_are_written():
    async with TestSessionLocal() as db:
        # A keyframe every 20 s draining at 0.01 %/s: continuous coverage.
        await store_readings(db, [reading(t, 90.0 - 0.01 * t, -0.01) for t in range(0, 600, 20)])
    now = (HOUR + timedelta(minutes=10, seconds=30)).timestamp()
    await RollupJob(TestSessionLocal).run_once(now)

    rows = await minutes()
    assert len(rows) == 10
    assert all(r.seconds_covered == pytest.approx(60) for r in rows)
    assert rows[0].battery_avg == pytest.approx(90.0 - 0.01 * 30)
    assert rows[0].distance_m == pytest.approx(60.0)
    async with TestSessionLocal() as db:
        hour = (await db.execute(select(TelemetryHour))).scalar_one()
    assert hour.seconds_covered == pytest.approx(600)
    assert hour.battery_avg == pytest.approx(90.0 - 0.01 * 300)
    assert hour.distance_m == pytest.approx(600.0)


async def test_a_rerun_is_idempotent():
    async with TestSessionLocal() as db:
        await store_readings(db, [reading(t, 80.0) for t in range(0, 300, 10)])
    now = (HOUR + timedelta(minutes=5, seconds=5)).timestamp()
    jobber = RollupJob(TestSessionLocal)
    await jobber.run_once(now)
    first = [(r.bucket, r.battery_avg) for r in await minutes()]
    await jobber.run_once(now)
    assert [(r.bucket, r.battery_avg) for r in await minutes()] == first


async def test_a_late_keyframe_recomputes_its_minute():
    async with TestSessionLocal() as db:
        await store_readings(db, [reading(t, 50.0) for t in range(0, 120, 10)])
    jobber = RollupJob(TestSessionLocal)
    first_run = (HOUR + timedelta(minutes=2, seconds=5)).timestamp()
    await jobber.run_once(first_run)
    assert (await minutes())[0].battery_avg == pytest.approx(50.0)

    # An hour later, with the job up to date, an outbox replay delivers a
    # keyframe for the very first minute: far outside the lookback.
    later = first_run + 3600
    await jobber.run_once(later - 60)
    late = [reading(5, 90.0)]
    async with TestSessionLocal() as db:
        stored = await store_readings(db, late)
    job.mark_dirty(stored, now=later)
    await jobber.run_once(later)
    # 50 % for the first 5 s, then 90 % for 5 s, then 50 % again.
    assert (await minutes())[0].battery_avg == pytest.approx((50 * 55 + 90 * 5) / 60)


async def test_the_api_serves_history(client):
    async with TestSessionLocal() as db:
        await store_readings(db, [reading(t, 70.0) for t in range(0, 180, 15)])
    await RollupJob(TestSessionLocal).run_once((HOUR + timedelta(minutes=3, seconds=1)).timestamp())
    response = await client.get(
        "/api/v1/robots/1/history",
        params={"start": HOUR.isoformat(), "end": (HOUR + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3 and body[0]["battery_avg"] == pytest.approx(70.0)
    hourly = await client.get(
        "/api/v1/robots/1/history",
        params={
            "start": HOUR.isoformat(),
            "end": (HOUR + timedelta(hours=1)).isoformat(),
            "resolution": "hour",
        },
    )
    assert len(hourly.json()) == 1 and hourly.json()[0]["seconds_covered"] == pytest.approx(180)
