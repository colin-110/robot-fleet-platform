"""
Robot service — fleet status derivation.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.telemetry_repo import TelemetryRepository

logger = logging.getLogger(__name__)


def _derive_status(latest, age_seconds: float) -> str:
    """Derive status with timeout fallbacks."""
    if age_seconds > 300:
        return "OFFLINE"
    
    if latest.status:
        if age_seconds > 60:
            return "OFFLINE"
        return latest.status.upper()
        
    return "ACTIVE"


def summarize_robot_history(rows) -> dict | None:
    """Condenses recent telemetry history into a single status payload."""
    if not rows:
        return None
        
    # Assume rows are ordered oldest to newest from the DB window function
    latest = rows[-1]
    
    now = datetime.now(timezone.utc)
    ts = latest.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
        
    age_seconds = (now - ts).total_seconds()
    if age_seconds > 180:
        return None
        
    status = _derive_status(latest, age_seconds)
    
    # Calculate linear drain rate over the history window
    runtime = None
    if len(rows) >= 2:
        oldest = rows[0]
        old_ts = oldest.timestamp
        if old_ts.tzinfo is None:
            old_ts = old_ts.replace(tzinfo=timezone.utc)
        else:
            old_ts = old_ts.astimezone(timezone.utc)
            
        dt_minutes = (ts - old_ts).total_seconds() / 60.0
        dbattery = oldest.battery - latest.battery
        
        if dt_minutes > 0 and dbattery > 0:
            drain_per_minute = dbattery / dt_minutes
            effective_battery = latest.battery * (latest.battery_health / 100.0)
            runtime = round(effective_battery / drain_per_minute, 1)

    mst = latest.mission_start_time
    if mst:
        if mst.tzinfo is None:
            mst = mst.replace(tzinfo=timezone.utc)
        else:
            mst = mst.astimezone(timezone.utc)

    return {
        "robot_id": latest.robot_id,
        "battery": round(latest.battery, 2),
        "temperature": round(latest.temperature, 2),
        "speed": round(latest.speed, 2),
        "status": status,
        "mission_id": latest.mission_id,
        "mission_type": latest.mission_type,
        "mission_progress": round(latest.mission_progress, 1) if latest.mission_progress is not None else None,
        "mission_start_time": mst.isoformat().replace("+00:00", "Z") if mst else None,
        "last_seen": ts.isoformat().replace("+00:00", "Z"),
        "runtime_remaining_minutes": runtime,
        "x": round(latest.x, 2) if latest.x is not None else 0.0,
        "y": round(latest.y, 2) if latest.y is not None else 0.0,
        "battery_health": round(latest.battery_health, 2) if latest.battery_health else 100.0,
        "motor_health": round(latest.motor_health, 2) if latest.motor_health else 100.0,
        "sensor_health": round(latest.sensor_health, 2) if latest.sensor_health else 100.0,
        "network_health": round(latest.network_health, 2) if latest.network_health else 100.0,
    }


class RobotService:
    """Derives current robot status from recent telemetry history."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)

    async def get_fleet_status(self) -> list[dict]:
        """
        Return the current status summary for every robot.
        Uses optimized per-robot queries instead of loading the full table.
        """
        grouped = await self.repo.get_recent_per_robot(per_robot_limit=30)

        robots = []
        for robot_id in sorted(grouped):
            summary = summarize_robot_history(grouped[robot_id])
            if summary is not None:
                robots.append(summary)

        logger.debug("Fleet status computed for %d robots", len(robots))
        return robots
