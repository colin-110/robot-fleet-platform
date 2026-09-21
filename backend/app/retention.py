"""
Telemetry retention pruning.

Shared between the worker's loop (``worker.py``'s ``db_pruner_task``) and a
loop the backend runs on its own (``main.py``'s lifespan). The backend needs
its own copy of this because the worker is an optional, separately-deployed
process — a deployment that skips it (this app's free-tier Render path, which
has no worker at all) would otherwise never prune anything. Telemetry grew
unbounded until it hit Neon's free-tier storage quota, at which point every
INSERT started failing with ``DiskFullError`` and ingestion silently stopped
for hours before anyone noticed.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Telemetry

logger = logging.getLogger(__name__)
settings = get_settings()


async def prune_old_telemetry() -> int:
    """Delete telemetry older than ``settings.retention_days``, in chunks.

    Chunked rather than one unbounded ``DELETE``: a single delete spanning a
    day or more of telemetry would hold locks long enough to stall ingestion.
    Returns the total number of rows deleted.
    """
    limit_date = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    total_deleted = 0

    while True:
        async with AsyncSessionLocal() as session, session.begin():
            subq = (
                select(Telemetry.id).where(Telemetry.timestamp < limit_date).limit(10000).subquery()
            )
            stmt = delete(Telemetry).where(Telemetry.id.in_(select(subq)))
            result = await session.execute(stmt)
            deleted_count = result.rowcount
            total_deleted += deleted_count

        if deleted_count == 0:
            break
        await asyncio.sleep(0.1)  # Yield to avoid locking the DB too hard

    return total_deleted


async def prune_loop(
    *, use_redis_lock: bool, initial_delay_seconds: float, interval_seconds: float
) -> None:
    """Run ``prune_old_telemetry`` on a fixed interval, forever.

    ``use_redis_lock`` guards against two processes pruning at the same
    moment — meaningful when a worker and this loop might both be running
    (the multi-instance/AWS path). A single-instance deployment with no
    coordination to do (``websocket_backend=direct``, no worker) skips the
    lock entirely rather than depending on Redis for something that doesn't
    need cross-process coordination when there's only one process.
    """
    await asyncio.sleep(initial_delay_seconds)

    while True:
        try:
            if use_redis_lock:
                from app.websocket_manager import manager

                lock_acquired = await manager.redis.set("lock:db_pruner", "1", nx=True, ex=3600)
                if not lock_acquired:
                    await asyncio.sleep(interval_seconds)
                    continue

            deleted = await prune_old_telemetry()
            if deleted:
                logger.info(
                    "RETENTION: Pruned %d telemetry records older than %d days.",
                    deleted,
                    settings.retention_days,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in retention pruner loop; retrying in 1 hour")
            await asyncio.sleep(3600)
            continue

        await asyncio.sleep(interval_seconds)
