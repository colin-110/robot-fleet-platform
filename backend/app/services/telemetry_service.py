"""
Telemetry service — ingest + broadcast + read.
"""

import logging
from datetime import timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Telemetry
from app.repositories.telemetry_repo import TelemetryRepository
from app.schemas import TelemetryCreate
from app.websocket_manager import manager

logger = logging.getLogger(__name__)


def _to_iso(value):
    """Convert a datetime to an ISO 8601 UTC string."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _telemetry_to_broadcast_dict(t: Telemetry) -> dict:
    """Serialize a Telemetry ORM object for WebSocket broadcast."""
    return {
        "robot_id": t.robot_id,
        "battery": t.battery,
        "temperature": t.temperature,
        "speed": t.speed,
        "status": t.status,
        "mission_id": t.mission_id,
        "mission_type": t.mission_type,
        "mission_progress": t.mission_progress,
        "mission_start_time": _to_iso(t.mission_start_time),
        "battery_health": t.battery_health,
        "motor_health": t.motor_health,
        "sensor_health": t.sensor_health,
        "network_health": t.network_health,
        "x": t.x,
        "y": t.y,
        "timestamp": _to_iso(t.timestamp),
    }


class TelemetryService:
    """Handles telemetry ingestion, broadcast, and retrieval."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)

    async def ingest(self, data: TelemetryCreate) -> dict:
        """Persist telemetry, broadcast to WebSocket clients, return ack."""
        telemetry = await self.repo.insert(data)
        logger.info(
            "Telemetry ingested: robot_id=%d battery=%.1f status=%s",
            telemetry.robot_id,
            telemetry.battery,
            telemetry.status,
        )

        # Broadcast to all connected dashboard clients
        await manager.broadcast(_telemetry_to_broadcast_dict(telemetry))

        return {"message": "Telemetry received", "id": telemetry.id}

    async def get_recent(self, limit: int = 50) -> list[Telemetry]:
        """Return the most recent telemetry rows."""
        return await self.repo.get_recent(limit)
