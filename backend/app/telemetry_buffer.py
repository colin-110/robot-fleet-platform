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
from app.repositories.robot_repo import RobotRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.schemas import TelemetryCreate

logger = logging.getLogger(__name__)

# Ceiling on readings held while the database is unreachable. At the demo
# fleet's rate this is well over an hour of telemetry; past it the oldest
# readings are dropped so a long outage can't exhaust the host's memory.
MAX_PENDING = 50_000

_pending: list[tuple[TelemetryCreate, datetime]] = []
_lock = asyncio.Lock()


async def buffer(rows: list[tuple[TelemetryCreate, datetime]]) -> None:
    async with _lock:
        _pending.extend(rows)


async def flush() -> int:
    """Write everything buffered since the last flush in one batch."""
    async with _lock:
        rows, _pending[:] = list(_pending), []

    if not rows:
        return 0

    try:
        async with AsyncSessionLocal() as session, session.begin():
            await TelemetryRepository(session).insert_many(rows)

            latest_by_robot: dict[int, datetime] = {}
            for data, ts in rows:
                if data.robot_id not in latest_by_robot or ts > latest_by_robot[data.robot_id]:
                    latest_by_robot[data.robot_id] = ts
            await RobotRepository(session).mark_seen(latest_by_robot)
    except BaseException:
        # Put the batch back rather than losing it. Retrying is safe even if
        # the COMMIT landed and only its acknowledgement was lost: the insert
        # skips readings already stored, so nothing is written twice.
        # BaseException so a shutdown cancelling a flush mid-write requeues
        # too; the final flush in main.py's lifespan then gets another try.
        await _requeue(rows)
        raise

    return len(rows)


async def _requeue(rows: list[tuple[TelemetryCreate, datetime]]) -> None:
    async with _lock:
        _pending[:0] = rows
        overflow = len(_pending) - MAX_PENDING
        if overflow > 0:
            del _pending[:overflow]
            logger.warning(
                "TELEMETRY BUFFER: dropped %d oldest readings; database unreachable "
                "and the buffer is at its %d-reading cap.",
                overflow,
                MAX_PENDING,
            )


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
