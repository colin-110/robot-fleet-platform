"""
In-process telemetry write buffering for the direct-ingest path.

Serverless Postgres (Neon) only accrues compute-hours while a query is
actually running, and suspends after 5 idle minutes on the free tier - fixed,
can't be raised. Free-tier compute-hours (100/month, roughly 400 hours at the
smallest size) are a tighter budget than storage once a simulator ingests
continuously: writing every reading immediately keeps a query running
essentially always, so compute never gets the chance to suspend at all.

This buffers rows in memory and flushes them in one batch on a timer instead
-- except while at least one dashboard viewer is connected, when
telemetry_service uses the normal immediate write path so the live view
stays accurate. Nobody is watching a REST snapshot that's briefly stale if
nobody is watching it at all.
"""

import asyncio
import logging
from datetime import datetime

from app.database import AsyncSessionLocal
from app.repositories.telemetry_repo import persist
from app.schemas import TelemetryCreate

logger = logging.getLogger(__name__)

_pending: list[tuple[TelemetryCreate, datetime]] = []
_lock = asyncio.Lock()


async def buffer(rows: list[tuple[TelemetryCreate, datetime]]) -> None:
    async with _lock:
        _pending.extend(rows)


async def take() -> list[tuple[TelemetryCreate, datetime]]:
    """Remove and return everything buffered."""
    async with _lock:
        rows, _pending[:] = list(_pending), []
    return rows


async def restore(rows: list[tuple[TelemetryCreate, datetime]]) -> None:
    """Put rows back at the front after a failed write, keeping their order."""
    if rows:
        async with _lock:
            _pending[:0] = rows


def oldest_pending(robot_id: int) -> datetime | None:
    """Earliest buffered timestamp for a robot, or None if nothing is buffered."""
    stamps = [ts for data, ts in _pending if data.robot_id == robot_id]
    return min(stamps) if stamps else None


async def flush() -> int:
    """Write everything buffered since the last flush in one batch."""
    rows = await take()
    if not rows:
        return 0

    try:
        async with AsyncSessionLocal() as session, session.begin():
            await persist(session, rows)
    except Exception:
        # Put the rows back so the next flush retries them. Writes skip
        # readings already stored, so a partial earlier attempt is harmless.
        await restore(rows)
        raise

    from app import durability

    durability.mark_stored((d.robot_id, ts) for d, ts in rows)
    return len(rows)


async def flush_loop(interval_seconds: float) -> None:
    """Flush on a fixed interval, forever. Longer than Neon's 5-minute
    suspend timeout, so compute actually gets an idle window to suspend in
    between flushes rather than being nudged awake just often enough to
    never sleep.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            flushed = await flush()
            if flushed:
                logger.info("TELEMETRY BUFFER: flushed %d buffered readings.", flushed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error flushing telemetry buffer; will retry next interval")
