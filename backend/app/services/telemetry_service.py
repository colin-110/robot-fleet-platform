"""
Telemetry service — ingest + broadcast + read.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Telemetry
from app.repositories.telemetry_repo import TelemetryRepository
from app.schemas import TelemetryCreate
from app.websocket_manager import manager
from fastapi import BackgroundTasks

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

    async def ingest(self, data: TelemetryCreate, background_tasks: BackgroundTasks = None) -> dict:
        """Persist telemetry, broadcast to WebSocket clients, return ack."""
        if settings.use_redis_buffer:
            ts_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload = data.model_dump(mode='json')
            payload["timestamp"] = ts_str

            # Broadcast immediately to WebSockets via stream (this also adds to
            # the Redis stream that the worker drains into PostgreSQL).
            if background_tasks:
                background_tasks.add_task(manager.broadcast, payload)
            else:
                await manager.broadcast(payload)

            return {"message": "Telemetry queued in Redis", "id": 0}
        else:
            telemetry = await self.repo.insert(data)

            if background_tasks:
                background_tasks.add_task(manager.broadcast, _telemetry_to_broadcast_dict(telemetry))
            else:
                await manager.broadcast(_telemetry_to_broadcast_dict(telemetry))
            return {"message": "Telemetry received", "id": telemetry.id}

    async def ingest_batch(self, data: list[TelemetryCreate], background_tasks: BackgroundTasks) -> dict:
        """Batch ingestion for high-throughput simulator pushing."""
        from app.main import telemetry_ingested_total
        telemetry_ingested_total.inc(len(data))

        ts_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        if settings.use_redis_buffer:
            payloads = []
            for d in data:
                payload = d.model_dump(mode='json')
                payload["timestamp"] = ts_str
                payloads.append(payload)

            if background_tasks:
                background_tasks.add_task(manager.broadcast_batch, payloads)
            else:
                await manager.broadcast_batch(payloads)

            return {"message": f"{len(data)} telemetry readings queued in Redis"}
        else:
            payloads = []
            for d in data:
                telemetry = await self.repo.insert(d)
                payloads.append(_telemetry_to_broadcast_dict(telemetry))

            if background_tasks:
                background_tasks.add_task(manager.broadcast_batch, payloads)
            else:
                await manager.broadcast_batch(payloads)

            return {"message": f"{len(data)} telemetry readings received"}

    async def get_recent(self, limit: int = 50, skip: int = 0) -> list[Telemetry]:
        """Return the most recent telemetry rows."""
        return await self.repo.get_recent(limit=limit, skip=skip)
