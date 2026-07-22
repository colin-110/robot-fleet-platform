import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.robot_service import RobotService, summarize_robot_history
from app.models import Telemetry
from datetime import datetime, timezone, timedelta

def test_summarize_robot_history_empty():
    assert summarize_robot_history([]) is None

def test_summarize_robot_history_single_row():
    now = datetime.now(timezone.utc)
    t = Telemetry(
        robot_id=1, battery=100.0, temperature=25.0, speed=1.0, status="ACTIVE",
        mission_id="m1", mission_type="patrol", mission_progress=50.0,
        mission_start_time=now, timestamp=now, x=10.0, y=20.0,
        battery_health=100.0, motor_health=100.0, sensor_health=100.0, network_health=100.0
    )
    summary = summarize_robot_history([t])
    assert summary["robot_id"] == 1
    assert summary["status"] == "ACTIVE"
    assert summary["battery"] == 100.0
    assert summary["runtime_remaining_minutes"] is None

def test_summarize_robot_history_drain_extrapolation():
    now = datetime.now(timezone.utc)
    t1 = Telemetry(
        robot_id=1, battery=100.0, temperature=25.0, speed=1.0, status="ACTIVE",
        timestamp=now - timedelta(minutes=10), battery_health=100.0
    )
    t2 = Telemetry(
        robot_id=1, battery=90.0, temperature=25.0, speed=1.0, status="ACTIVE",
        timestamp=now, battery_health=100.0
    )
    summary = summarize_robot_history([t1, t2])
    # Drain = 10% in 10 minutes = 1% per minute
    # Effective battery = 90.0
    # Runtime = 90.0 / 1.0 = 90.0 minutes
    assert summary["runtime_remaining_minutes"] == 90.0

def test_summarize_robot_history_offline_timeout():
    old_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    t = Telemetry(
        robot_id=1, battery=100.0, temperature=25.0, speed=1.0, status="ACTIVE",
        timestamp=old_time, battery_health=100.0
    )
    summary = summarize_robot_history([t])
    assert summary["status"] == "OFFLINE"

@pytest.mark.asyncio
async def test_get_fleet_status_caching():
    db = AsyncMock()
    service = RobotService(db)
    
    with patch("app.cache.cache.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [{"robot_id": 1, "status": "ACTIVE"}]
        
        result = await service.get_fleet_status()
        assert len(result) == 1
        assert result[0]["robot_id"] == 1
        mock_get.assert_called_once()
