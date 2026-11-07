from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Robot, RobotState
from app.services.robot_service import RobotService, summarize_state


def state(**overrides) -> RobotState:
    now = datetime.now(timezone.utc)
    values = {
        "robot_id": 1,
        "battery": 100.0,
        "temperature": 25.0,
        "speed": 1.0,
        "status": "ACTIVE",
        "mission_id": "m1",
        "mission_type": "patrol",
        "mission_progress": 50.0,
        "mission_start_time": now,
        "timestamp": now,
        "x": 10.0,
        "y": 20.0,
        "battery_health": 100.0,
        "motor_health": 100.0,
        "sensor_health": 100.0,
        "network_health": 100.0,
        "battery_drain_per_min": None,
    }
    values.update(overrides)
    return RobotState(**values)


def test_summarize_state():
    summary = summarize_state(state())
    assert summary["robot_id"] == 1
    assert summary["status"] == "ACTIVE"
    assert summary["battery"] == 100.0
    assert summary["runtime_remaining_minutes"] is None


def test_runtime_comes_from_the_drain_rate():
    summary = summarize_state(state(battery=90.0, battery_drain_per_min=1.0))
    # 90% effective battery at 1% per minute
    assert summary["runtime_remaining_minutes"] == 90.0


def test_runtime_accounts_for_battery_health():
    summary = summarize_state(state(battery=90.0, battery_health=50.0, battery_drain_per_min=1.0))
    assert summary["runtime_remaining_minutes"] == 45.0


@pytest.mark.parametrize("drain", [0.0, -0.5])
def test_no_runtime_when_the_battery_is_not_falling(drain):
    assert summarize_state(state(battery_drain_per_min=drain))["runtime_remaining_minutes"] is None


def test_old_state_is_offline():
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    assert summarize_state(state(timestamp=old))["status"] == "OFFLINE"


@pytest.mark.asyncio
async def test_get_fleet_status_lists_roster_even_with_no_telemetry():
    """The fleet list comes from the roster, not from whoever reported recently."""
    service = RobotService(AsyncMock())

    with patch.object(service.robots, "list_with_state", new_callable=AsyncMock) as roster:
        roster.return_value = [
            (Robot(id=1, is_active=True, last_seen=None), None),
            (Robot(id=2, is_active=True, last_seen=None), None),
        ]

        result = await service.get_fleet_status()

    assert [r["robot_id"] for r in result] == [1, 2]
    assert all(r["status"] == "OFFLINE" for r in result)


@pytest.mark.asyncio
async def test_state_older_than_the_window_is_shown_as_silent():
    """Stale numbers are not shown next to an OFFLINE badge."""
    service = RobotService(AsyncMock())
    stale = state(timestamp=datetime.now(timezone.utc) - timedelta(hours=2), battery=55.0)

    with patch.object(service.robots, "list_with_state", new_callable=AsyncMock) as roster:
        roster.return_value = [(Robot(id=1, is_active=True, last_seen=stale.timestamp), stale)]
        [summary] = await service.get_fleet_status()

    assert summary["status"] == "OFFLINE"
    assert summary["battery"] == 0.0
