"""The budget controller end to end: observed rates in, desired tolerances out."""

import pytest

from app.budget import controller as budget
from app.budget.controller import BudgetController, scale_of, tolerances_for
from app.intelligence import fleet
from app.intelligence.fleet import RobotModel
from app.mqtt import config as mqtt_config
from app.mqtt import predict, wire
from app.repositories import config_repo
from tests.conftest import TestSessionLocal
from tests.test_robot_config import RecordingPublisher

NOW = 1_800_000_000.0


def busy_robot(robot_id: int, per_minute: float) -> None:
    model = fleet.robots().setdefault(robot_id, RobotModel())
    model.first_seen = NOW - 300
    model.last = {"t": NOW - 1, "temperature": 35.0, "battery": 80.0}
    count = int(per_minute * 5)
    model.keyframes.extend(NOW - 300 + i * 300 / count for i in range(count))


@pytest.fixture
def publisher(monkeypatch):
    recording = RecordingPublisher()
    monkeypatch.setattr(mqtt_config, "publisher", recording)
    return recording


def test_tolerances_scale_together():
    tol = tolerances_for(2.0)
    assert tol["tol_position_m"] == 2 * predict.DEFAULTS["tol_position_m"]
    assert tol["tol_battery_pct"] == 2 * predict.DEFAULTS["tol_battery_pct"]
    assert "heartbeat_s" not in tol
    assert scale_of(tol) == pytest.approx(2.0)
    assert scale_of(None) == 1.0


async def test_nothing_happens_while_the_budget_is_off(publisher):
    busy_robot(1, 20)
    assert await BudgetController(TestSessionLocal).step(NOW) == {}
    assert publisher.sent() == []


async def test_an_over_budget_fleet_gets_looser_tolerances(client, publisher):
    for robot_id in range(1, 11):
        busy_robot(robot_id, 20)  # 200 a minute in all
    response = await client.put(
        "/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 50}
    )
    assert response.status_code == 200
    controller = BudgetController(TestSessionLocal)
    changed = await controller.step(NOW)
    assert len(changed) == 10 and all(s > 1 for s in changed.values())
    assert controller.last["planned_per_min"] == pytest.approx(50, rel=0.01)
    _, (version, values), _, retained = publisher.sent()[0]
    assert retained and version == 1
    assert values["tol_position_m"] > predict.DEFAULTS["tol_position_m"]


async def test_other_settings_are_kept(client, publisher):
    await client.put("/api/v1/robots/1/config", json={"values": {"report_interval_s": 2}})
    busy_robot(1, 40)
    await client.put("/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 10})
    await BudgetController(TestSessionLocal).step(NOW)
    async with TestSessionLocal() as db:
        desired = (await config_repo.get_config(db, 1)).desired
    assert desired["report_interval_s"] == 2.0
    assert "tol_position_m" in desired
    # Whatever the allocator chose is also valid for the robot.
    assert wire.check_config(desired)[1] == {}


async def test_a_robot_with_an_alarm_is_watched_more_closely(client, publisher, monkeypatch):
    for robot_id in (1, 2):
        busy_robot(robot_id, 20)

    class Engine:
        _open = {(1, "BATTERY_LOW"): 1}

    monkeypatch.setattr(budget.alarm_engine, "engine", Engine())
    await client.put("/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 12})
    changed = await BudgetController(TestSessionLocal).step(NOW)
    assert changed[1] < changed[2]


async def test_a_steady_fleet_sends_no_config(client, publisher):
    for robot_id in range(1, 4):
        busy_robot(robot_id, 20)
    await client.put("/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 30})
    controller = BudgetController(TestSessionLocal)
    await controller.step(NOW)
    sent = len(publisher.sent())
    # The robots report applying the new tolerances, and their rates follow.
    async with TestSessionLocal() as db:
        for robot_id in range(1, 4):
            row = await config_repo.get_config(db, robot_id)
            await config_repo.set_reported(db, robot_id, row.version, row.desired, {})
        await db.commit()
    scale = scale_of((await client.get("/api/v1/robots/1/config")).json()["desired"])
    fleet.reset()
    for robot_id in range(1, 4):
        busy_robot(robot_id, 2 + 18 / scale**2)
    assert await controller.step(NOW) == {}
    assert len(publisher.sent()) == sent


async def test_a_changed_robot_is_measured_afresh(client, publisher):
    busy_robot(1, 40)
    await client.put("/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 5})
    controller = BudgetController(TestSessionLocal)
    assert 1 in await controller.step(NOW)
    assert fleet.get(1).rate_per_minute(NOW + 30) is None
    assert await controller.step(NOW + 30) == {}


async def test_robots_not_measured_still_count_against_the_budget(client, publisher):
    for robot_id in range(1, 11):
        busy_robot(robot_id, 20)
    await client.put("/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 50})
    controller = BudgetController(TestSessionLocal)
    first = await controller.step(NOW)
    # Robots 2 to 10 are being measured afresh; robot 1 is measured again.
    busy_robot(1, 2 + 18 / first[1] ** 2)
    fleet.get(1).first_seen = NOW - 300
    await controller.step(NOW)
    # Robot 1 must not be handed the whole budget the others are still using.
    assert controller.last["planned_per_min"] == pytest.approx(50, rel=0.05)
    assert 1 not in controller.last.get("changed_ids", [])


async def test_an_unreachable_budget_is_reported(client, publisher):
    # Robots whose rate does not fall with tolerance: measured at two scales.
    for robot_id in range(1, 6):
        busy_robot(robot_id, 10)
    await client.put("/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 10})
    controller = BudgetController(TestSessionLocal)
    await controller.step(NOW)
    for robot_id in range(1, 6):
        fleet.get(robot_id).keyframes.clear()
        busy_robot(robot_id, 10)
    await controller.step(NOW)
    assert controller.last["feasible"] is False
    # The fitted floor (10 a minute each) is what the plan expects.
    assert controller.last["planned_per_min"] == pytest.approx(50, rel=0.05)


async def test_the_budget_api_validates(client):
    assert (await client.get("/api/v1/fleet/budget")).json()["enabled"] is False
    bad = await client.put("/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 0})
    assert bad.status_code == 422


async def test_what_is_published_is_what_was_stored(client, publisher):
    # A robot that already has settings: the controller loads them first, and
    # the new row must not come back as the stale, already-loaded one.
    await client.put("/api/v1/robots/1/config", json={"values": {"tol_position_m": 0.5}})
    publisher.client.published.clear()
    busy_robot(1, 40)
    await client.put("/api/v1/fleet/budget", json={"enabled": True, "messages_per_minute": 10})
    await BudgetController(TestSessionLocal).step(NOW)
    async with TestSessionLocal() as db:
        stored = await config_repo.get_config(db, 1)
    [(_, (version, values), _, _)] = publisher.sent()
    assert (version, values) == (stored.version, stored.desired) == (2, stored.desired)
    assert values["tol_position_m"] != 0.5
