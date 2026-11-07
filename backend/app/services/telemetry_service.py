"""
Telemetry service - ingest + broadcast + read.

Two ingest paths, selected by ``OPT_REDIS_BUFFER``:

**Buffered (default).** The request handler does a single Redis ``XADD`` and
returns. A separate worker process drains the stream and bulk-inserts into
PostgreSQL. Request latency is decoupled from database I/O, so an ingest spike
queues in Redis instead of blocking on disk.

**Direct.** ``INSERT`` + ``COMMIT`` inline on the request path, no worker
needed.
"""

import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry_stream
from app.config import get_settings
from app.models import Telemetry
from app.repositories.telemetry_repo import TelemetryRepository, persist
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


def resolve_timestamp(reported: datetime | None, received_at: datetime) -> datetime:
    """Decide the authoritative time for a reading.

    Device time is preferred, because only the device knows when it actually
    took the measurement - and a batch uploaded after a network outage would
    otherwise collapse onto its upload time, destroying the timeline.

    Two guards. A reading dated far in the future is clamped to server time: a
    device with a wrong clock would otherwise land in a time bucket that
    analytics has already summarized, or sit permanently at the head of "most
    recent" and make a dead robot look alive. Backdated readings are accepted
    as-is, since that is exactly what legitimate store-and-forward looks like.
    """
    if reported is None:
        return received_at

    if reported.tzinfo is None:
        reported = reported.replace(tzinfo=timezone.utc)
    reported = reported.astimezone(timezone.utc)

    skew = (reported - received_at).total_seconds()
    if skew > settings.max_clock_skew_seconds:
        logger.warning(
            "Telemetry timestamp %s is %.0fs ahead of server time; clamping. "
            "Check the device clock.",
            reported.isoformat(),
            skew,
        )
        return received_at

    return reported


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


def _reading_to_dict(data: TelemetryCreate, timestamp: datetime) -> dict:
    """JSON-ready reading with its resolved timestamp (stream and broadcast)."""
    payload = data.model_dump(mode="json")
    payload["timestamp"] = _to_iso(timestamp)
    return payload


class TelemetryService:
    """Handles telemetry ingestion, broadcast, and retrieval."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)

    async def ingest(self, data: TelemetryCreate, background_tasks: BackgroundTasks = None) -> dict:
        """Ingest one reading. Same paths as ``ingest_batch``."""
        mode, stored = await self._ingest([data], background_tasks)
        if mode == "queued":
            return {"message": "Telemetry queued in Redis"}
        if not stored:
            return {"message": "Telemetry already received"}
        return {"message": "Telemetry received"}

    async def ingest_batch(
        self, data: list[TelemetryCreate], background_tasks: BackgroundTasks = None
    ) -> dict:
        """Ingest a batch of readings."""
        mode, stored = await self._ingest(data, background_tasks)
        if mode == "queued":
            message = f"{len(stored)} telemetry readings queued in Redis"
        else:
            message = f"{len(stored)} telemetry readings received"
        duplicates = len(data) - len(stored)
        if duplicates:
            message += f", {duplicates} already stored"
        return {"message": message}

    async def _ingest(
        self, data: list[TelemetryCreate], background_tasks: BackgroundTasks | None
    ) -> tuple[str, list]:
        """Route readings to one of two paths and broadcast the new ones.

        - ``queued``: added to the Redis stream; the worker persists them.
        - ``direct``: written now, in one transaction.

        Returns the path taken and the readings that were new (queued
        payloads or inserted rows). Readings already stored are skipped.
        """
        received_at = datetime.now(timezone.utc)
        # Resolved per reading: stamping a whole batch with one time would
        # collapse readings taken seconds apart onto a single instant.
        rows = [(d, resolve_timestamp(d.timestamp, received_at)) for d in data]

        if settings.opt_redis_buffer:
            # Awaited, not a background task: the response tells the robot the
            # readings are safe, so they must be on the stream first.
            queued = await telemetry_stream.enqueue([_reading_to_dict(d, ts) for d, ts in rows])
            if settings.websocket_backend == "direct":
                # The Redis listener is off in this mode, so fan out here.
                await self._broadcast(queued, background_tasks)
            return "queued", queued

        inserted = await persist(self.repo.db, rows)
        await self.repo.db.commit()

        # Only new rows are broadcast, so a retried upload is not shown twice.
        await self._broadcast([_telemetry_to_broadcast_dict(t) for t in inserted], background_tasks)
        return "direct", inserted

    @staticmethod
    async def _broadcast(payloads: list[dict], background_tasks: BackgroundTasks | None) -> None:
        if not payloads:
            return
        if background_tasks:
            background_tasks.add_task(manager.broadcast_batch, payloads)
        else:
            await manager.broadcast_batch(payloads)

    async def get_recent(self, limit: int = 50, skip: int = 0) -> list[Telemetry]:
        """Return the most recent telemetry rows."""
        return await self.repo.get_recent(limit=limit, skip=skip)
