"""Alarm rules, hysteresis, the lifecycle and its API."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import presence
from app.alarms.engine import AlarmEngine
from app.alarms.rules import RULES, Tracker
from app.intelligence import fleet
from app.intelligence.fleet import RobotModel
from app.models import Alarm
from tests.conftest import TestSessionLocal

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
RULE = {r.kind: r for r in RULES}


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def set_robot(robot_id: int, when: datetime, **last) -> None:
    model = fleet.robots().setdefault(robot_id, RobotModel())
    model.last = {
        "t": when.timestamp(),
        "battery": 80.0,
        "temperature": 35.0,
        "health": 90.0,
        **last,
    }


@pytest.fixture
def engine():
    e = AlarmEngine(session_factory=TestSessionLocal)
    e.events = []

    async def record(event, alarm):
        e.events.append((event, alarm["kind"], alarm["state"]))

    e.listeners.append(record)
    return e


async def open_alarms(kind=None):
    async with TestSessionLocal() as db:
        stmt = select(func.count()).select_from(Alarm).where(Alarm.state != "CLEARED")
        if kind:
            stmt = stmt.where(Alarm.kind == kind)
        return (await db.execute(stmt)).scalar_one()


def test_hysteresis_raises_once_and_clears_only_past_the_clear_threshold():
    rule, tracker = RULE["BATTERY_LOW"], Tracker()
    # Below 20 but not yet for the hold time.
    assert tracker.step(rule, 1, 19.0, 0.0, False) is None
    assert tracker.step(rule, 1, 19.0, 5.0, False) is None
    # A bounce back above resets the hold.
    assert tracker.step(rule, 1, 21.0, 6.0, False) is None
    assert tracker.step(rule, 1, 19.0, 7.0, False) is None
    assert tracker.step(rule, 1, 19.0, 17.0, False) == "raise"
    # Open: 22 is above the raise level but below the clear level.
    assert tracker.step(rule, 1, 22.0, 18.0, True) is None
    assert tracker.step(rule, 1, 26.0, 19.0, True) == "clear"


async def test_a_low_battery_raises_one_alarm_and_clears(engine):
    set_robot(5, at(0), battery=15.0)
    await engine.evaluate(at(0))
    set_robot(5, at(11), battery=14.0)
    await engine.evaluate(at(11))
    await engine.evaluate(at(12))
    assert await open_alarms("BATTERY_LOW") == 1
    assert ("raised", "BATTERY_LOW", "ACTIVE") in engine.events

    set_robot(5, at(20), battery=40.0)
    await engine.evaluate(at(20))
    assert await open_alarms() == 0
    assert engine.events[-1] == ("cleared", "BATTERY_LOW", "CLEARED")


async def test_open_alarms_survive_an_engine_restart(engine):
    set_robot(6, at(0), temperature=90.0)
    await engine.evaluate(at(0))
    await engine.evaluate(at(10))
    assert await open_alarms("OVERHEATING") == 1

    restarted = AlarmEngine(session_factory=TestSessionLocal)
    set_robot(6, at(11), temperature=91.0)
    await restarted.evaluate(at(11))
    assert await open_alarms("OVERHEATING") == 1
    set_robot(6, at(12), temperature=50.0)
    await restarted.evaluate(at(12))
    assert await open_alarms() == 0


async def test_offline_is_held_so_a_quick_reconnect_raises_nothing(engine):
    presence.apply(7, False, None, now=at(0))
    await engine.evaluate(at(0))
    presence.apply(7, True, "c2", now=at(10))
    await engine.evaluate(at(10))
    await engine.evaluate(at(40))
    assert await open_alarms("OFFLINE") == 0

    presence.apply(7, False, "c2", now=at(50))
    await engine.evaluate(at(50))
    await engine.evaluate(at(81))
    assert await open_alarms("OFFLINE") == 1


async def test_a_robot_running_twice_is_flagged(engine):
    for i in range(6):
        presence.apply(8, True, f"conn-{i}", now=at(i * 5))
    await engine.evaluate(at(30))
    assert await open_alarms("DUPLICATE_SESSION") == 1


async def test_stale_readings_neither_raise_nor_clear(engine):
    set_robot(9, at(0), battery=5.0)
    await engine.evaluate(at(0))
    assert await open_alarms("BATTERY_CRITICAL") == 1
    # Ten minutes later nothing new has arrived: the alarm stays, nothing else is raised.
    await engine.evaluate(at(600))
    assert await open_alarms("BATTERY_CRITICAL") == 1
    # BATTERY_LOW was still in its hold time when the readings went stale.
    assert await open_alarms() == 1


async def test_the_api_lists_and_acknowledges(client, engine):
    set_robot(10, at(0), battery=5.0)
    await engine.evaluate(at(0))
    listed = (await client.get("/api/v1/alarms")).json()
    alarm = next(a for a in listed if a["kind"] == "BATTERY_CRITICAL")
    assert alarm["state"] == "ACTIVE"

    acked = await client.post(f"/api/v1/alarms/{alarm['id']}/ack")
    assert acked.status_code == 200
    assert acked.json()["state"] == "ACKNOWLEDGED"
    assert acked.json()["acknowledged_by"] == "anonymous"
    # Acknowledged is still open, and cannot be acknowledged again.
    assert (await client.post(f"/api/v1/alarms/{alarm['id']}/ack")).status_code == 409
    assert (await client.post("/api/v1/alarms/999999/ack")).status_code == 404

    set_robot(10, at(5), battery=60.0)
    await engine.evaluate(at(5))
    assert (await client.get("/api/v1/alarms")).json() == []
    everything = (await client.get("/api/v1/alarms", params={"state": "all"})).json()
    assert {a["state"] for a in everything} == {"CLEARED"}
