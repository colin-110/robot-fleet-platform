"""Keeping telemetry_lines current: one-hour blocks, recomputed whole.

Every ``INTERVAL`` seconds, each hour that ended at least ``SETTLE`` ago and
is not done yet is turned into segments for every robot and field
(``lines.lines_for``), checked against the stored path (``lines.worst_error``),
and written in place of that hour's previous segments, in one transaction.
Re-running an hour therefore changes nothing, and a crash mid-run loses
nothing. Segments never cross an hour's edge; that costs one extra segment per
field per hour, and makes every hour independent.

A keyframe that arrives for an hour already done (a robot's outbox replayed
after an outage) marks that robot dirty from that hour (``mark_dirty``, called
by ingest after each commit), and the next run redoes it. After a restart the
job resumes from the hour it saved.

The tolerance for each field is the robot's own (its applied settings, the
same ones it sends keyframes by) times ``LINE_TOLERANCE_SCALE``. The total
error against what the robot measured is at most the robot's tolerance plus
this one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import metrics
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import FleetSetting, Telemetry, TelemetryLine
from app.mqtt import predict
from app.repositories import config_repo
from app.storage import lines

logger = logging.getLogger(__name__)
settings = get_settings()

BLOCK = 3600.0
# How long after an hour ends before it is done: late keyframes within this
# go in on the first pass instead of causing a redo.
SETTLE = 120.0
INTERVAL = 300.0
WATERMARK_KEY = "lines"
# Where a fresh install starts, and the most a restarted job catches up.
FIRST_RUN_BACKFILL = 6 * 3600.0
MAX_CATCH_UP = 48 * 3600.0
# Which tolerance setting each field is held to.
FIELD_TOLERANCE = {
    "x": "tol_position_m",
    "y": "tol_position_m",
    "battery": "tol_battery_pct",
    "temperature": "tol_temperature_c",
    "speed": "tol_speed",
    "mission_progress": "tol_progress_pct",
    "battery_health": "tol_health_pct",
    "motor_health": "tol_health_pct",
    "sensor_health": "tol_health_pct",
    "network_health": "tol_health_pct",
}

# Hour start -> robots whose segments in that hour need redoing (filled by ingest).
_dirty: dict[float, set[int]] = {}
# The hour up to which segments are done; ingest compares against it.
_done_until: float | None = None


def mark_dirty(rows) -> None:
    """Called with newly stored rows; remembers ones in hours already done.

    A keyframe changes its own hour, and the start of the next one if it is
    still in force there (it can be, for up to a heartbeat with its grace).
    """
    if _done_until is None:
        return
    for row in rows:
        t = row.timestamp.timestamp()
        hour = t // BLOCK * BLOCK
        for h in (hour, hour + BLOCK):
            if h < _done_until:
                _dirty.setdefault(h, set()).add(row.robot_id)


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


def _keyframe(row: Telemetry) -> dict:
    return {
        "timestamp": row.timestamp.timestamp(),
        **{name: getattr(row, name) for name in lines.FIELDS},
        "vx": row.vx,
        "vy": row.vy,
        "battery_rate": row.battery_rate,
    }


def tolerances(applied: dict | None, scale: float) -> tuple[dict[str, float], float]:
    """Each field's tolerance and the heartbeat, from a robot's applied settings."""
    tol = predict.tolerances(applied)
    return {name: tol[key] * scale for name, key in FIELD_TOLERANCE.items()}, tol["heartbeat_s"]


class LinesJob:
    def __init__(self, session_factory=AsyncSessionLocal, scale: float | None = None) -> None:
        self.session_factory = session_factory
        self.scale = settings.line_tolerance_scale if scale is None else scale
        self.stats = {
            "runs": 0,
            "hours": 0,
            "keyframes_read": 0,
            "lines_written": 0,
            # A segment further from the stored path than its tolerance. Never
            # expected; counted and logged so a bug cannot pass silently.
            "verify_failures": 0,
        }

    async def block(self, db, start: float, robot_ids=None, applied=None) -> int:
        """Redo the segments of the hour starting at ``start``. Returns lines written."""
        end = start + BLOCK
        applied = applied if applied is not None else await config_repo.applied_settings(db)
        # The keyframe in force at the hour's start began at most a heartbeat
        # (with its grace) earlier; anything older has run out.
        longest = max(
            [tolerances(applied.get(r), self.scale)[1] for r in applied]
            + [predict.DEFAULTS["heartbeat_s"]]
        )
        query = (
            select(Telemetry)
            .where(
                Telemetry.timestamp >= _dt(start - longest * lines.HEARTBEAT_GRACE),
                Telemetry.timestamp < _dt(end),
            )
            .order_by(Telemetry.robot_id, Telemetry.timestamp)
        )
        if robot_ids is not None:
            query = query.where(Telemetry.robot_id.in_(list(robot_ids)))
        by_robot: dict[int, list[dict]] = {}
        for row in (await db.execute(query)).scalars():
            by_robot.setdefault(row.robot_id, []).append(_keyframe(row))
        self.stats["keyframes_read"] += sum(len(f) for f in by_robot.values())

        rows = []
        for robot_id, frames in by_robot.items():
            field_tol, heartbeat = tolerances(applied.get(robot_id), self.scale)
            for name, field_id in lines.FIELD_IDS.items():
                tol = field_tol[name]
                found = lines.lines_for(frames, name, end, heartbeat, tol, start=start)
                runs = lines.path(frames, name, end, heartbeat, start)
                error = lines.worst_error(found, runs)
                if error > tol * (1 + 1e-9) + 1e-9:
                    self.stats["verify_failures"] += 1
                    logger.error(
                        "Segments for robot %d %s in the hour from %s are %.4f from the "
                        "stored path, over the tolerance %.4f",
                        robot_id,
                        name,
                        _dt(start).isoformat(),
                        error,
                        tol,
                    )
                rows.extend(
                    {
                        "robot_id": robot_id,
                        "field": field_id,
                        "t0": _dt(ln.t0),
                        "t1": _dt(ln.t1),
                        "v0": ln.v0,
                        "slope": ln.slope,
                    }
                    for ln in found
                )

        old = delete(TelemetryLine).where(
            TelemetryLine.t0 >= _dt(start), TelemetryLine.t0 < _dt(end)
        )
        if robot_ids is not None:
            old = old.where(TelemetryLine.robot_id.in_(list(robot_ids)))
        await db.execute(old)
        table = TelemetryLine.__table__
        for i in range(0, len(rows), 1000):
            await db.execute(pg_insert(table), rows[i : i + 1000])
        self.stats["hours"] += 1
        self.stats["lines_written"] += len(rows)
        return len(rows)

    async def run_once(self, now: float | None = None) -> int:
        """Do every finished hour not done yet, and any redone by late data."""
        global _done_until
        now = time.time() if now is None else now
        last_complete = (now - SETTLE) // BLOCK * BLOCK  # hours before this are finished
        written = 0
        async with self.session_factory() as db:
            saved = await db.get(FleetSetting, WATERMARK_KEY)
            until = saved.value["until"] if saved else last_complete - FIRST_RUN_BACKFILL
            until = max(until, last_complete - MAX_CATCH_UP)
            _done_until = until
            applied = await config_repo.applied_settings(db)

            for hour in sorted(_dirty):
                robots = _dirty.pop(hour)
                if hour < until:
                    written += await self.block(db, hour, robots, applied)

            start = until
            while start + BLOCK <= last_complete:
                written += await self.block(db, start, None, applied)
                start += BLOCK
            if start != until or not saved:
                stmt = pg_insert(FleetSetting).values(
                    key=WATERMARK_KEY, value={"until": start}, updated_at=_dt(now)
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["key"],
                    set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
                )
                await db.execute(stmt)
            await db.commit()
            _done_until = start
        self.stats["runs"] += 1
        return written

    async def run(self, interval: float = INTERVAL) -> None:
        while True:
            started = time.perf_counter()
            try:
                await self.run_once()
                metrics.LINES_RUN_SECONDS.observe(time.perf_counter() - started)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Line segments job failed")
            await asyncio.sleep(interval)
