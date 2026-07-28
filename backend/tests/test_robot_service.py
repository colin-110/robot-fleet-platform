import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.models import Robot, Telemetry
from app.services.robot_service import RobotService, summarize_robot_history


def test_summarize_robot_history_empty():
    assert summarize_robot_history([]) is None


def test_summarize_robot_history_single_row():
    now = datetime.now(timezone.utc)
    t = Telemetry(
        robot_id=1,
        battery=100.0,
        temperature=25.0,
        speed=1.0,
        status="ACTIVE",
        mission_id="m1",
        mission_type="patrol",
        mission_progress=50.0,
        mission_start_time=now,
        timestamp=now,
        x=10.0,
        y=20.0,
        battery_health=100.0,
        motor_health=100.0,
        sensor_health=100.0,
        network_health=100.0,
    )
    summary = summarize_robot_history([t])
    assert summary["robot_id"] == 1
    assert summary["status"] == "ACTIVE"
    assert summary["battery"] == 100.0
    assert summary["runtime_remaining_minutes"] is None


def test_summarize_robot_history_drain_extrapolation():
    now = datetime.now(timezone.utc)
    t1 = Telemetry(
        robot_id=1,
        battery=100.0,
        temperature=25.0,
        speed=1.0,
        status="ACTIVE",
        timestamp=now - timedelta(minutes=10),
        battery_health=100.0,
    )
    t2 = Telemetry(
        robot_id=1,
        battery=90.0,
        temperature=25.0,
        speed=1.0,
        status="ACTIVE",
        timestamp=now,
        battery_health=100.0,
    )
    summary = summarize_robot_history([t1, t2])
    # Drain = 10% in 10 minutes = 1% per minute
    # Effective battery = 90.0
    # Runtime = 90.0 / 1.0 = 90.0 minutes
    assert summary["runtime_remaining_minutes"] == 90.0


def test_summarize_robot_history_offline_timeout():
    old_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    t = Telemetry(
        robot_id=1,
        battery=100.0,
        temperature=25.0,
        speed=1.0,
        status="ACTIVE",
        timestamp=old_time,
        battery_health=100.0,
    )
    summary = summarize_robot_history([t])
    assert summary["status"] == "OFFLINE"


@pytest.fixture
def read_cache_enabled():
    """Turn the read-through cache on for a single test.

    conftest pins ``opt_read_cache`` off so most tests assert on freshly
    computed values; the cache tests below opt back in.
    """
    settings = get_settings()
    original = settings.opt_read_cache
    settings.opt_read_cache = True
    yield
    settings.opt_read_cache = original


@pytest.mark.asyncio
async def test_get_fleet_status_serves_from_cache(read_cache_enabled):
    """A cache hit short-circuits before any database work."""
    db = AsyncMock()
    service = RobotService(db)

    with patch("app.cache.cache.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [{"robot_id": 1, "status": "ACTIVE"}]

        result = await service.get_fleet_status()

        assert result == [{"robot_id": 1, "status": "ACTIVE"}]
        mock_get.assert_called_once()
        db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_fleet_status_bypasses_cache_when_disabled():
    """With OPT_READ_CACHE off the cache is not consulted at all.

    Guards the benchmark: if the disabled path still read from Redis, the
    'no cache' arm would silently measure cache hits and report a bogus 0%.
    """
    service = RobotService(AsyncMock())

    with (
        patch("app.cache.cache.get", new_callable=AsyncMock) as mock_get,
        patch("app.cache.cache.set", new_callable=AsyncMock) as mock_set,
        patch.object(service.repo, "get_recent_per_robot", new_callable=AsyncMock) as mock_repo,
        patch.object(service.robots, "list_active", new_callable=AsyncMock) as mock_roster,
    ):
        mock_repo.return_value = {}
        mock_roster.return_value = []

        result = await service.get_fleet_status()

        assert result == []
        mock_get.assert_not_called()
        mock_set.assert_not_called()
        mock_repo.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_fleet_status_lists_roster_even_with_no_telemetry():
    """The fleet list comes from the roster, not from whoever reported recently.

    Guards the regression directly: with an empty telemetry result the old
    implementation returned nothing, silently hiding every robot in the fleet.
    """
    service = RobotService(AsyncMock())

    with (
        patch.object(service.repo, "get_recent_per_robot", new_callable=AsyncMock) as mock_repo,
        patch.object(service.robots, "list_active", new_callable=AsyncMock) as mock_roster,
    ):
        mock_repo.return_value = {}
        mock_roster.return_value = [
            Robot(id=1, is_active=True, last_seen=None),
            Robot(id=2, is_active=True, last_seen=None),
        ]

        result = await service.get_fleet_status()

        assert [r["robot_id"] for r in result] == [1, 2]
        assert all(r["status"] == "OFFLINE" for r in result)
