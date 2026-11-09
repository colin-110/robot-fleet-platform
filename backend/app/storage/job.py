"""Keeping the rollups current.

Every ``INTERVAL`` seconds the last ``LOOKBACK`` of minutes is recomputed
for every robot (an idempotent upsert), which also absorbs keyframes that
arrive a little late. A keyframe older than that (an outbox replayed after
an outage) marks its robot dirty from that minute, and the next run
recomputes from there. The hours touched are then rebuilt from their minutes.
After a restart the job resumes from the watermark it saved.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import metrics
from app.database import AsyncSessionLocal
from app.models import FleetSetting, Telemetry, TelemetryMinute
from app.mqtt import predict
from app.repositories import config_repo
from app.storage.rollups import minute_buckets

logger = logging.getLogger(__name__)

INTERVAL = 60.0
LOOKBACK = 600.0
WATERMARK_KEY = "rollups"
# The most a restarted job catches up in one run.
MAX_CATCH_UP = 6 * 3600.0

# robot_id -> earliest minute that needs recomputing (filled by ingest).
_dirty: dict[int, float] = {}


def mark_dirty(rows, now: float | None = None) -> None:
    """Called with newly stored rows; remembers ones older than the lookback."""
    now = time.time() if now is None else now
    for row in rows:
        t = row.timestamp.timestamp()
        if t < now - LOOKBACK + INTERVAL:
            minute = t // 60 * 60
            _dirty[row.robot_id] = min(_dirty.get(row.robot_id, minute), minute)


def _keyframe(row: Telemetry) -> dict:
    return {
        "timestamp": row.timestamp.timestamp(),
        "battery": row.battery,
        "battery_rate": row.battery_rate,
        "vx": row.vx,
        "vy": row.vy,
        "temperature": row.temperature,
        "speed": row.speed,
        "battery_health": row.battery_health,
        "motor_health": row.motor_health,
        "sensor_health": row.sensor_health,
        "network_health": row.network_health,
    }


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


async def _upsert(db, model, rows: list[dict]) -> None:
    if not rows:
        return
    for start in range(0, len(rows), 1000):
        stmt = pg_insert(model).values(rows[start : start + 1000])
        stmt = stmt.on_conflict_do_update(
            index_elements=["robot_id", "bucket"],
            set_={c: stmt.excluded[c] for c in rows[0] if c not in ("robot_id", "bucket")},
        )
        await db.execute(stmt)


async def rollup_robot(db, robot_id: int, start: float, end: float, heartbeat_s: float) -> int:
    """Recompute the minutes of one robot in ``[start, end)``; returns rows written."""
    before = (
        select(Telemetry)
        .where(Telemetry.robot_id == robot_id, Telemetry.timestamp < _dt(start))
        .order_by(Telemetry.timestamp.desc())
        .limit(1)
    )
    within = (
        select(Telemetry)
        .where(
            Telemetry.robot_id == robot_id,
            Telemetry.timestamp >= _dt(start),
            Telemetry.timestamp < _dt(end),
        )
        .order_by(Telemetry.timestamp)
    )
    rows = [*(await db.execute(before)).scalars(), *(await db.execute(within)).scalars()]
    buckets = minute_buckets([_keyframe(r) for r in rows], start, end, heartbeat_s)
    out = [{**b.row(robot_id), "bucket": _dt(t)} for t, b in buckets.items()]
    await _upsert(db, TelemetryMinute, out)
    return len(out)


HOUR_SQL = text(
    """
    INSERT INTO telemetry_1h AS h (
        robot_id, bucket, seconds_covered, keyframes, distance_m,
        battery_avg, battery_min, battery_max, temperature_avg, temperature_max,
        speed_avg, battery_health_avg, motor_health_avg, sensor_health_avg,
        network_health_avg
    )
    SELECT robot_id, date_trunc('hour', bucket), sum(seconds_covered), sum(keyframes),
           sum(distance_m),
           sum(battery_avg * seconds_covered) / nullif(sum(seconds_covered)
               FILTER (WHERE battery_avg IS NOT NULL), 0),
           min(battery_min), max(battery_max),
           sum(temperature_avg * seconds_covered) / nullif(sum(seconds_covered)
               FILTER (WHERE temperature_avg IS NOT NULL), 0),
           max(temperature_max),
           sum(speed_avg * seconds_covered) / nullif(sum(seconds_covered)
               FILTER (WHERE speed_avg IS NOT NULL), 0),
           sum(battery_health_avg * seconds_covered) / nullif(sum(seconds_covered)
               FILTER (WHERE battery_health_avg IS NOT NULL), 0),
           sum(motor_health_avg * seconds_covered) / nullif(sum(seconds_covered)
               FILTER (WHERE motor_health_avg IS NOT NULL), 0),
           sum(sensor_health_avg * seconds_covered) / nullif(sum(seconds_covered)
               FILTER (WHERE sensor_health_avg IS NOT NULL), 0),
           sum(network_health_avg * seconds_covered) / nullif(sum(seconds_covered)
               FILTER (WHERE network_health_avg IS NOT NULL), 0)
    FROM telemetry_1m
    WHERE bucket >= date_trunc('hour', CAST(:start AS timestamptz))
      AND bucket < date_trunc('hour', CAST(:end AS timestamptz)) + interval '1 hour'
    GROUP BY robot_id, date_trunc('hour', bucket)
    ON CONFLICT (robot_id, bucket) DO UPDATE SET
        seconds_covered = excluded.seconds_covered, keyframes = excluded.keyframes,
        distance_m = excluded.distance_m, battery_avg = excluded.battery_avg,
        battery_min = excluded.battery_min, battery_max = excluded.battery_max,
        temperature_avg = excluded.temperature_avg,
        temperature_max = excluded.temperature_max, speed_avg = excluded.speed_avg,
        battery_health_avg = excluded.battery_health_avg,
        motor_health_avg = excluded.motor_health_avg,
        sensor_health_avg = excluded.sensor_health_avg,
        network_health_avg = excluded.network_health_avg
    """
)


class RollupJob:
    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self.session_factory = session_factory
        self.stats = {"runs": 0, "minutes_written": 0}

    async def run_once(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        end = now // 60 * 60  # the current minute is still filling
        async with self.session_factory() as db:
            saved = await db.get(FleetSetting, WATERMARK_KEY)
            until = saved.value["until"] if saved else end - LOOKBACK
            start = max(min(until, end) - LOOKBACK, end - MAX_CATCH_UP) // 60 * 60
            applied = await config_repo.applied_settings(db)
            robot_rows = await db.execute(
                select(Telemetry.robot_id)
                .where(Telemetry.timestamp >= _dt(start - 3600))
                .group_by(Telemetry.robot_id)
            )
            robots = {r for (r,) in robot_rows} | set(_dirty)
            dirty = {r: _dirty.pop(r) for r in list(_dirty)}
            written, earliest = 0, start
            for robot_id in sorted(robots):
                heartbeat = predict.tolerances(applied.get(robot_id))["heartbeat_s"]
                robot_start = min(start, dirty.get(robot_id, start))
                earliest = min(earliest, robot_start)
                written += await rollup_robot(db, robot_id, robot_start, end, heartbeat)
            await db.execute(HOUR_SQL, {"start": _dt(earliest), "end": _dt(end)})
            stmt = pg_insert(FleetSetting).values(
                key=WATERMARK_KEY, value={"until": end}, updated_at=_dt(now)
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
            )
            await db.execute(stmt)
            await db.commit()
        self.stats["runs"] += 1
        self.stats["minutes_written"] += written
        return written

    async def run(self, interval: float = INTERVAL) -> None:
        while True:
            started = time.perf_counter()
            try:
                await self.run_once()
                metrics.ROLLUP_RUN_SECONDS.observe(time.perf_counter() - started)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Rollup failed")
            await asyncio.sleep(interval)


async def coverage(db) -> int:
    """Minutes stored, for health and tests."""
    return (await db.execute(select(func.count()).select_from(TelemetryMinute))).scalar_one()
