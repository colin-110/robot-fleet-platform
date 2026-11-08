"""
Telemetry service - ingest + broadcast + read.

Readings are written in one transaction through ``persist()``, which skips
readings already stored, then broadcast to dashboards.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.intelligence import fleet
from app.models import Telemetry
from app.repositories.telemetry_repo import TelemetryRepository, persist
from app.schemas import TelemetryCreate
from app.storage import job as rollups
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
        "vx": t.vx,
        "vy": t.vy,
        "battery_rate": t.battery_rate,
        "timestamp": _to_iso(t.timestamp),
    }


async def persist_readings(db: AsyncSession, data: list[TelemetryCreate]) -> list[Telemetry]:
    """Write readings without committing. The one write path for telemetry.

    Returns the rows actually inserted; readings already stored are skipped,
    so a redelivered or retried reading is harmless.
    """
    if not data:
        return []
    received_at = datetime.now(timezone.utc)
    return await persist(db, device_times(data, received_at))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def device_times(
    data: list[TelemetryCreate], received_at: datetime
) -> list[tuple[TelemetryCreate, datetime]]:
    """Each reading with the time to store it under.

    Device time, as reported. But when a robot's newest reading in the batch
    is further in the future than ``max_clock_skew_seconds``, its clock is
    fast, and every one of its readings moves back by the same amount, so the
    newest lands on the receive time. Clamping each reading to the receive
    time instead collapsed a batch taken seconds apart onto one instant, and
    all but one were then dropped as duplicates.
    """
    ahead: dict[int, float] = {}
    for d in data:
        skew = (_utc(d.timestamp) - received_at).total_seconds()
        ahead[d.robot_id] = max(ahead.get(d.robot_id, skew), skew)
    rows = []
    for d in data:
        if ahead[d.robot_id] > settings.max_clock_skew_seconds:
            fast_by = timedelta(seconds=ahead[d.robot_id])
            rows.append((d, _utc(d.timestamp) - fast_by))
        else:
            rows.append((d, resolve_timestamp(d.timestamp, received_at)))
    return rows


async def store_readings(db: AsyncSession, data: list[TelemetryCreate]) -> list[Telemetry]:
    """``persist_readings`` and commit."""
    inserted = await persist_readings(db, data)
    await db.commit()
    fleet.observe(inserted)
    rollups.mark_dirty(inserted)
    return inserted


def broadcast_payloads(rows: list[Telemetry]) -> list[dict]:
    return [_telemetry_to_broadcast_dict(t) for t in rows]


class TelemetryService:
    """Handles telemetry ingestion, broadcast, and retrieval."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)

    async def ingest(self, data: TelemetryCreate, background_tasks: BackgroundTasks = None) -> dict:
        """Ingest one reading. Same path as ``ingest_batch``."""
        stored = await self._ingest([data], background_tasks)
        return {"message": "Telemetry received" if stored else "Telemetry already received"}

    async def ingest_batch(
        self, data: list[TelemetryCreate], background_tasks: BackgroundTasks = None
    ) -> dict:
        """Ingest a batch of readings."""
        stored = await self._ingest(data, background_tasks)
        message = f"{len(stored)} telemetry readings received"
        if len(data) > len(stored):
            message += f", {len(data) - len(stored)} already stored"
        return {"message": message}

    async def _ingest(
        self, data: list[TelemetryCreate], background_tasks: BackgroundTasks | None
    ) -> list[Telemetry]:
        """Store readings and broadcast the new ones."""
        inserted = await store_readings(self.repo.db, data)
        # Only new rows are broadcast, so a retried upload is not shown twice.
        await self._broadcast(broadcast_payloads(inserted), background_tasks)
        return inserted

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
