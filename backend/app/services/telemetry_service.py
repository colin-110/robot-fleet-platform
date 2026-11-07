"""
Telemetry service - ingest + broadcast + read.

Two ingest paths, selected by ``OPT_REDIS_BUFFER``:

**Buffered (default).** The request handler does a single Redis ``XADD`` and
returns. A separate worker process drains the stream and bulk-inserts into
PostgreSQL. Request latency is decoupled from database I/O, so an ingest spike
queues in Redis instead of blocking on disk.

**Direct.** ``INSERT`` + ``COMMIT`` inline on the request path, no worker
needed. This is what the single-instance EC2 deployment runs; with
``DEFER_WRITES_WITHOUT_VIEWERS`` it batches writes in memory while nobody is
watching (see ``app/telemetry_buffer.py``).
"""

import logging
from datetime import datetime, timezone

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry_buffer
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
        mode, inserted = await self._ingest([data], background_tasks)
        if mode == "queued":
            return {"message": "Telemetry queued in Redis", "id": 0}
        if mode == "buffered":
            return {"message": "Telemetry buffered", "id": 0}
        if not inserted:
            return {"message": "Telemetry already received", "id": None}
        return {"message": "Telemetry received", "id": inserted[0].id}

    async def ingest_batch(
        self, data: list[TelemetryCreate], background_tasks: BackgroundTasks = None
    ) -> dict:
        """Ingest a batch of readings."""
        mode, inserted = await self._ingest(data, background_tasks)
        if mode == "queued":
            return {"message": f"{len(data)} telemetry readings queued in Redis"}
        if mode == "buffered":
            return {"message": f"{len(data)} telemetry readings buffered"}
        duplicates = len(data) - len(inserted)
        message = f"{len(inserted)} telemetry readings received"
        if duplicates:
            message += f", {duplicates} already stored"
        return {"message": message}

    async def _ingest(
        self, data: list[TelemetryCreate], background_tasks: BackgroundTasks | None
    ) -> tuple[str, list[Telemetry]]:
        """Route readings to one of three paths and broadcast them.

        - ``queued``: XADD to the Redis stream; the worker persists it.
        - ``direct``: written now, in one transaction.
        - ``buffered``: held in memory and flushed later, because nobody is
          watching (DEFER_WRITES_WITHOUT_VIEWERS, see telemetry_buffer.py).

        Returns the path taken and, for ``direct``, the rows actually
        inserted (duplicates of already-stored readings are skipped).
        """
        received_at = datetime.now(timezone.utc)
        # Resolved per reading: stamping a whole batch with one time would
        # collapse readings taken seconds apart onto a single instant.
        rows = [(d, resolve_timestamp(d.timestamp, received_at)) for d in data]

        if settings.opt_redis_buffer:
            # The XADD both feeds dashboards and queues the rows for the worker.
            await self._broadcast([_reading_to_dict(d, ts) for d, ts in rows], background_tasks)
            return "queued", []

        if settings.defer_writes_without_viewers and manager.connection_count == 0:
            await telemetry_buffer.buffer(rows)
            await self._broadcast([_reading_to_dict(d, ts) for d, ts in rows], background_tasks)
            return "buffered", []

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
