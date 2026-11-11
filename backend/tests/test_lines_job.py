"""The line segments job against the database: hours, re-runs, late data, restarts."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import TelemetryLine
from app.schemas import TelemetryCreate
from app.services.telemetry_service import store_readings
from app.storage import lines, lines_job
from app.storage.lines_job import LinesJob
from tests.conftest import TestSessionLocal

HOUR = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
BATTERY = lines.FIELD_IDS["battery"]
TEMPERATURE = lines.FIELD_IDS["temperature"]


def reading(seconds, battery=None, rate=None, temperature=40.0, robot_id=1):
    return TelemetryCreate(
        robot_id=robot_id,
        battery=battery,
        battery_rate=rate,
        temperature=temperature,
        speed=1.0,
        vx=1.0,
        vy=0.0,
        x=0.0,
        y=0.0,
        timestamp=HOUR + timedelta(seconds=seconds),
    )


def after(hours=1, seconds=180):
    """A moment ``hours`` after HOUR plus enough to let the hour settle."""
    return (HOUR + timedelta(hours=hours, seconds=seconds)).timestamp()


async def segments(field, robot_id=1):
    async with TestSessionLocal() as db:
        rows = await db.execute(
            select(TelemetryLine)
            .where(TelemetryLine.robot_id == robot_id, TelemetryLine.field == field)
            .order_by(TelemetryLine.t0)
        )
        return list(rows.scalars())


def value_at(rows, when):
    t = when.timestamp()
    for row in rows:
        if row.t0.timestamp() <= t <= row.t1.timestamp():
            return row.v0 + row.slope * (t - row.t0.timestamp())
    return None


@pytest.fixture(autouse=True)
def fresh_state():
    lines_job._dirty.clear()
    lines_job._done_until = None
    yield
    lines_job._dirty.clear()
    lines_job._done_until = None


async def steady_hour():
    async with TestSessionLocal() as db:
        # A keyframe every 20 s draining at 0.01 %/s, for the whole hour.
        await store_readings(db, [reading(t, 90.0 - 0.01 * t, -0.01) for t in range(0, 3600, 20)])


async def test_an_hour_becomes_a_few_segments_within_tolerance():
    await steady_hour()
    job = LinesJob(TestSessionLocal)
    await job.run_once(after())

    battery = await segments(BATTERY)
    assert len(battery) == 1  # a steady drain is one line for the hour
    assert value_at(battery, HOUR + timedelta(minutes=30)) == pytest.approx(72.0, abs=1.0)
    assert len(await segments(TEMPERATURE)) == 1  # held still: one flat line
    assert job.stats["verify_failures"] == 0
    assert job.stats["keyframes_read"] == 180


async def test_running_again_changes_nothing():
    await steady_hour()
    job = LinesJob(TestSessionLocal)
    async with TestSessionLocal() as db:
        first = await job.block(db, HOUR.timestamp())
        await db.commit()
        again = await job.block(db, HOUR.timestamp())
        await db.commit()
        count = (await db.execute(select(func.count()).select_from(TelemetryLine))).scalar_one()
    assert first == again == count


async def test_an_unfinished_hour_waits():
    await steady_hour()
    # One minute after the hour: inside the settle time, so not done yet.
    await LinesJob(TestSessionLocal).run_once(after(seconds=60))
    assert await segments(BATTERY) == []


async def test_a_late_keyframe_redoes_its_hour():
    await steady_hour()
    job = LinesJob(TestSessionLocal)
    await job.run_once(after())
    assert len(await segments(TEMPERATURE)) == 1

    # An outbox replayed after an outage: a hot spell inside the finished hour.
    async with TestSessionLocal() as db:
        await store_readings(
            db,
            [reading(t + 5, 90.0 - 0.01 * (t + 5), -0.01, temperature=70.0) for t in (1200, 1220)],
        )
    assert lines_job._dirty  # ingest marked it
    await job.run_once(after(seconds=240))

    temperature = await segments(TEMPERATURE)
    assert len(temperature) >= 3  # 40, then 70, then 40 again
    assert value_at(temperature, HOUR + timedelta(seconds=1215)) == pytest.approx(70.0, abs=1.0)
    assert job.stats["verify_failures"] == 0


async def test_a_restart_resumes_where_it_stopped():
    await steady_hour()
    await LinesJob(TestSessionLocal).run_once(after())
    restarted = LinesJob(TestSessionLocal)
    await restarted.run_once(after(seconds=200))
    assert restarted.stats["hours"] == 0  # nothing new to do


async def test_robots_are_held_to_their_own_tolerance_times_the_scale():
    async with TestSessionLocal() as db:
        # A battery that wobbles by 0.8 % around its drain, as a noisy gauge does.
        await store_readings(
            db,
            [
                reading(t, 90.0 - 0.01 * t + (0.8 if t % 40 else 0.0), -0.01)
                for t in range(0, 3600, 20)
            ],
        )
    tight, loose = LinesJob(TestSessionLocal, scale=0.01), LinesJob(TestSessionLocal, scale=3)
    async with TestSessionLocal() as db:
        many = await tight.block(db, HOUR.timestamp())
        few = await loose.block(db, HOUR.timestamp())
        await db.rollback()
    assert many > few
