"""
Telemetry service — ingest + broadcast + read.
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Telemetry
from app.repositories.telemetry_repo import TelemetryRepository
from app.schemas import TelemetryCreate
from app.websocket_manager import manager

logger = logging.getLogger(__name__)
settings = get_settings()


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
        if settings.use_redis_buffer:
            ts_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload = {
                "robot_id": data.robot_id,
                "battery": data.battery,
                "temperature": data.temperature,
                "speed": data.speed,
                "status": data.status,
                "mission_id": data.mission_id,
                "mission_type": data.mission_type,
                "mission_progress": data.mission_progress,
                "mission_start_time": data.mission_start_time.isoformat().replace("+00:00", "Z") if data.mission_start_time else None,
                "battery_health": data.battery_health,
                "motor_health": data.motor_health,
                "sensor_health": data.sensor_health,
                "network_health": data.network_health,
                "x": data.x,
                "y": data.y,
                "timestamp": ts_str,
            }
            
            # Queue to Redis for background worker ingestion
            try:
                await manager.redis.lpush("telemetry_queue", json.dumps(payload))
            except Exception:
                logger.exception("Failed to push telemetry to Redis queue")
            
            # Broadcast immediately to WebSockets via stream
            await manager.broadcast(payload)
            
            return {"message": "Telemetry queued in Redis", "id": 0}
        else:
            telemetry = await self.repo.insert(data)
            await manager.broadcast(_telemetry_to_broadcast_dict(telemetry))
            return {"message": "Telemetry received", "id": telemetry.id}

    async def get_recent(self, limit: int = 50) -> list[Telemetry]:
        """Return the most recent telemetry rows."""
        return await self.repo.get_recent(limit)
