"""Dropping old raw keyframes, safely, once line segments hold their history.

Every ``INTERVAL`` seconds, whole chunks of ``telemetry`` older than the drop
line are dropped. The drop line is the earliest of:

- ``RAW_RETENTION_DAYS`` ago (30 by default, the old fixed policy);
- the hour the line segments job has reached: nothing is dropped before its
  segments exist;
- the earliest hour with a segment that failed verification: its raw
  keyframes stay until a redo passes.

Before dropping, the keyframes within ``INCIDENT_MARGIN_MINUTES`` of any
alarm on the same robot are copied to ``telemetry_incidents`` (kept a year),
so an incident keeps its exact data for investigation. Copying is idempotent.

TimescaleDB drops a chunk only when all of it is older than the line, so with
daily chunks raw data is kept up to a day longer than set.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import FleetSetting, Telemetry
from app.storage import lines_job

logger = logging.getLogger(__name__)

INTERVAL = 3600.0

ARCHIVE_SQL = text(
    """
    INSERT INTO telemetry_incidents
    SELECT DISTINCT t.*
    FROM telemetry t
    JOIN alarms a ON a.robot_id = t.robot_id
    WHERE t.timestamp < :line
      AND t.timestamp >= a.raised_at - make_interval(secs => :margin)
      AND t.timestamp <= coalesce(a.cleared_at, now()) + make_interval(secs => :margin)
    ON CONFLICT DO NOTHING
    """
)


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


async def drop_line(db, now: float, retention_days: float) -> tuple[float, str]:
    """Where raw keyframes may be dropped up to, and what decided it."""
    candidates = [(now - retention_days * 86400.0, "retention")]
    saved = await db.get(FleetSetting, lines_job.WATERMARK_KEY)
    candidates.append((saved.value["until"] if saved else float("-inf"), "segments not done"))
    failed = await lines_job.failed_hours(db)
    if failed:
        candidates.append((min(failed), "segments failed verification"))
    return min(candidates)


class RetentionJob:
    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self.session_factory = session_factory
        self.stats = {"runs": 0, "chunks_dropped": 0, "incident_rows_kept": 0}
        self.last_reason: str | None = None

    async def run_once(self, now: float | None = None) -> int:
        """Archive incidents and drop old chunks. Returns chunks dropped."""
        settings = get_settings()
        now = time.time() if now is None else now
        async with self.session_factory() as db:
            line, reason = await drop_line(db, now, settings.raw_retention_days)
            self.last_reason = reason
            if line == float("-inf"):
                self.stats["runs"] += 1
                return 0
            oldest = (await db.execute(select(func.min(Telemetry.timestamp)))).scalar()
            if oldest is None or oldest.timestamp() >= line:
                self.stats["runs"] += 1
                return 0
            kept = await db.execute(
                ARCHIVE_SQL, {"line": _dt(line), "margin": settings.incident_margin_minutes * 60}
            )
            self.stats["incident_rows_kept"] += kept.rowcount or 0
            dropped = await db.execute(
                text(
                    "SELECT count(*) FROM drop_chunks('telemetry', older_than => CAST(:line AS timestamptz))"
                ),
                {"line": _dt(line)},
            )
            count = dropped.scalar_one()
            await db.commit()
        self.stats["runs"] += 1
        self.stats["chunks_dropped"] += count
        if count:
            logger.info(
                "Dropped %d chunks of raw keyframes older than %s (limited by: %s); "
                "%d keyframes around alarms kept",
                count,
                _dt(line).isoformat(),
                reason,
                kept.rowcount or 0,
            )
        return count

    async def run(self, interval: float = INTERVAL) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Raw retention failed")
            await asyncio.sleep(interval)
