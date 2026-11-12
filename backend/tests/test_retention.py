"""Dropping old raw keyframes only when their segments hold the history."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import Alarm, Telemetry, telemetry_incidents
from app.schemas import TelemetryCreate
from app.services.telemetry_service import store_readings
from app.storage import lines_job
from app.storage.retention import RetentionJob
from tests.conftest import TestSessionLocal

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=40)
RECENT = NOW - timedelta(days=1)
settings = get_settings()


@pytest.fixture(autouse=True)
async def hypertable():
    """Daily chunks, as in production (the test schema is plain tables)."""
    async with TestSessionLocal() as db:
        await db.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        await db.execute(
            text(
                "SELECT create_hypertable('telemetry', by_range('timestamp', INTERVAL '1 day'), "
                "migrate_data => true)"
            )
        )
        await db.commit()


def reading(when, robot_id=1):
    return TelemetryCreate(
        robot_id=robot_id, battery=50.0, temperature=40.0, speed=1.0, timestamp=when
    )


async def setup_data(alarm=True):
    async with TestSessionLocal() as db:
        await store_readings(
            db,
            [reading(OLD + timedelta(minutes=m)) for m in range(0, 120, 2)]
            + [reading(OLD + timedelta(minutes=m), robot_id=2) for m in range(0, 120, 2)]
            + [reading(RECENT + timedelta(minutes=m)) for m in range(0, 60, 2)],
        )
        if alarm:
            db.add(
                Alarm(
                    robot_id=1,
                    kind="OVERHEATING",
                    severity="critical",
                    state="CLEARED",
                    message="Temperature 80 C",
                    raised_at=OLD + timedelta(minutes=60),
                    cleared_at=OLD + timedelta(minutes=70),
                )
            )
        await db.commit()


async def segments_done_until(when):
    async with TestSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO fleet_settings (key, value, updated_at) VALUES "
                "(:key, CAST(:value AS json), now())"
            ),
            {"key": lines_job.WATERMARK_KEY, "value": f'{{"until": {when.timestamp()}}}'},
        )
        await db.commit()


async def counts():
    async with TestSessionLocal() as db:
        raw = (await db.execute(select(func.count()).select_from(Telemetry))).scalar_one()
        kept = (
            await db.execute(select(func.count()).select_from(telemetry_incidents))
        ).scalar_one()
        return raw, kept


async def test_nothing_goes_before_its_segments_exist():
    await setup_data()
    job = RetentionJob(TestSessionLocal)
    assert await job.run_once(NOW.timestamp()) == 0
    assert job.last_reason == "segments not done"
    assert (await counts())[0] == 150


async def test_old_raw_keyframes_go_and_an_incident_is_kept():
    await setup_data()
    await segments_done_until(NOW)
    job = RetentionJob(TestSessionLocal)
    dropped = await job.run_once(NOW.timestamp())

    raw, kept = await counts()
    assert dropped >= 1
    assert raw == 30  # only the recent day is left
    # Robot 1's keyframes from 10 minutes before the alarm to 10 after it
    # cleared: minutes 50 to 80, every 2 minutes. Robot 2 had no alarm.
    assert kept == 16
    async with TestSessionLocal() as db:
        robots = (await db.execute(select(telemetry_incidents.c.robot_id).distinct())).scalars()
        assert list(robots) == [1]


async def test_running_again_keeps_nothing_twice():
    await setup_data()
    await segments_done_until(NOW)
    job = RetentionJob(TestSessionLocal)
    await job.run_once(NOW.timestamp())
    await job.run_once(NOW.timestamp())
    assert (await counts())[1] == 16


async def test_an_hour_that_failed_verification_keeps_its_raw_keyframes():
    await setup_data()
    await segments_done_until(NOW)
    async with TestSessionLocal() as db:
        await lines_job.failed_hours_save(db, [OLD.timestamp()])
        await db.commit()
    job = RetentionJob(TestSessionLocal)
    assert await job.run_once(NOW.timestamp()) == 0
    assert job.last_reason == "segments failed verification"
    assert (await counts())[0] == 150


async def test_the_retention_setting_is_respected(monkeypatch):
    await setup_data()
    await segments_done_until(NOW)
    monkeypatch.setattr(settings, "raw_retention_days", 60.0)
    assert await RetentionJob(TestSessionLocal).run_once(NOW.timestamp()) == 0
    assert (await counts())[0] == 150
