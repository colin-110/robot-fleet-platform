"""Retiring a robot: off the fleet view, key revoked, alarms cleared, history kept."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import presence
from app.alarms import engine as alarm_engine
from app.alarms.engine import AlarmEngine
from app.config import get_settings
from app.models import Alarm, Robot, Telemetry
from app.schemas import TelemetryCreate
from app.services.telemetry_service import store_readings
from tests.conftest import TestSessionLocal

MASTER = {"X-API-Key": get_settings().telemetry_api_key}


@pytest.fixture
async def engine():
    alarm_engine.engine = AlarmEngine(TestSessionLocal)
    yield alarm_engine.engine
    alarm_engine.engine = None


async def a_robot_with_an_open_alarm(robot_id=7):
    now = datetime.now(timezone.utc)
    async with TestSessionLocal() as db:
        await store_readings(
            db,
            [
                TelemetryCreate(
                    robot_id=robot_id,
                    battery=50.0,
                    temperature=40.0,
                    speed=0.0,
                    timestamp=now - timedelta(seconds=s),
                )
                for s in (10, 5)
            ],
        )
        db.add(
            Alarm(
                robot_id=robot_id,
                kind="OFFLINE",
                severity="critical",
                state="ACTIVE",
                message="Disconnected from the broker",
                raised_at=now,
            )
        )
        await db.commit()
    presence.apply(robot_id, False, "c1")


async def test_a_retired_robot_leaves_the_fleet_and_its_alarms_clear(client, engine):
    await a_robot_with_an_open_alarm()
    assert (await client.delete("/api/v1/robots/7", headers=MASTER)).status_code == 204

    async with TestSessionLocal() as db:
        robot = await db.get(Robot, 7)
        assert robot.is_active is False and robot.decommissioned_at is not None
        assert robot.key_hash is None
        open_alarms = (
            await db.execute(select(func.count()).where(Alarm.state != "CLEARED"))
        ).scalar_one()
        assert open_alarms == 0
        # Its history is kept.
        assert (await db.execute(select(func.count()).select_from(Telemetry))).scalar_one() == 2
    assert presence.get(7) is None
    fleet_ids = [r["robot_id"] for r in (await client.get("/api/v1/robots/status")).json()]
    assert 7 not in fleet_ids


async def test_the_alarm_engine_raises_nothing_for_a_retired_robot(client, engine):
    await a_robot_with_an_open_alarm()
    await client.delete("/api/v1/robots/7", headers=MASTER)
    presence.apply(7, False, "c2")  # an old retained message turns up again
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert await engine.evaluate(later) == []


async def test_a_new_key_brings_a_robot_back(client, engine):
    await a_robot_with_an_open_alarm()
    await client.delete("/api/v1/robots/7", headers=MASTER)
    assert (await client.post("/api/v1/robots/7/key", headers=MASTER)).status_code == 200
    async with TestSessionLocal() as db:
        assert (await db.get(Robot, 7)).is_active is True


async def test_retiring_needs_an_admin(client):
    await a_robot_with_an_open_alarm()
    assert (await client.delete("/api/v1/robots/7")).status_code == 403


async def test_retiring_an_unknown_robot_is_a_404(client):
    assert (await client.delete("/api/v1/robots/999", headers=MASTER)).status_code == 404
