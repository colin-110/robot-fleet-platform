"""
Telemetry service — ingest + broadcast + read.

Two ingest paths, selected by ``OPT_REDIS_BUFFER``:

**Buffered (default).** The request handler does a single Redis ``XADD`` and
returns. A separate worker process drains the stream and bulk-inserts into
PostgreSQL. Request latency is decoupled from database I/O, so an ingest spike
queues in Redis instead of blocking on disk.

**Direct (baseline).** ``INSERT`` + ``COMMIT`` inline on the request path. Every
robot's reading holds a connection from the pool for a full database round trip.
Retained so the benchmark can measure what the buffer actually saves.
"""

import logging
import time
from datetime import datetime, timezone

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.metrics import (
    db_write_latency_seconds,
    telemetry_ingest_seconds,
    telemetry_ingested_total,
)
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

    async def ingest(self, data: TelemetryCreate, background_tasks: BackgroundTasks = None) -> dict:
        """Persist telemetry, broadcast to WebSocket clients, return ack."""
        buffered = settings.opt_redis_buffer
        started = time.perf_counter()

        try:
            if buffered:
                payload = data.model_dump(mode="json")
                payload["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                # XADD both publishes to dashboards and queues the row for the
                # worker that writes it to PostgreSQL — one hop, not two.
                if background_tasks:
                    background_tasks.add_task(manager.broadcast, payload)
                else:
                    await manager.broadcast(payload)

                result = {"message": "Telemetry queued in Redis", "id": 0}
            else:
                db_started = time.perf_counter()
                telemetry = await self.repo.insert(data)
                db_write_latency_seconds.labels(mode="direct").observe(
                    time.perf_counter() - db_started
                )

                broadcast = _telemetry_to_broadcast_dict(telemetry)
                if background_tasks:
                    background_tasks.add_task(manager.broadcast, broadcast)
                else:
                    await manager.broadcast(broadcast)

                result = {"message": "Telemetry received", "id": telemetry.id}
        finally:
            telemetry_ingest_seconds.labels(path="single", buffered=str(buffered).lower()).observe(
                time.perf_counter() - started
            )

        telemetry_ingested_total.labels(path="single").inc()
        return result

    async def ingest_batch(
        self, data: list[TelemetryCreate], background_tasks: BackgroundTasks
    ) -> dict:
        """Batch ingestion for high-throughput simulator pushing."""
        buffered = settings.opt_redis_buffer
        started = time.perf_counter()
        ts_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            if buffered:
                payloads = []
                for d in data:
                    payload = d.model_dump(mode="json")
                    payload["timestamp"] = ts_str
                    payloads.append(payload)

                if background_tasks:
                    background_tasks.add_task(manager.broadcast_batch, payloads)
                else:
                    await manager.broadcast_batch(payloads)

                result = {"message": f"{len(data)} telemetry readings queued in Redis"}
            else:
                db_started = time.perf_counter()
                payloads = []
                for d in data:
                    telemetry = await self.repo.insert(d)
                    payloads.append(_telemetry_to_broadcast_dict(telemetry))
                db_write_latency_seconds.labels(mode="direct").observe(
                    time.perf_counter() - db_started
                )

                if background_tasks:
                    background_tasks.add_task(manager.broadcast_batch, payloads)
                else:
                    await manager.broadcast_batch(payloads)

                result = {"message": f"{len(data)} telemetry readings received"}
        finally:
            telemetry_ingest_seconds.labels(path="batch", buffered=str(buffered).lower()).observe(
                time.perf_counter() - started
            )

        telemetry_ingested_total.labels(path="batch").inc(len(data))
        return result

    async def get_recent(self, limit: int = 50, skip: int = 0) -> list[Telemetry]:
        """Return the most recent telemetry rows."""
        return await self.repo.get_recent(limit=limit, skip=skip)
