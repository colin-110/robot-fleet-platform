"""
Telemetry repository - all database queries in one place.

This replaces the pattern of loading the *entire* telemetry table into
memory on every request.  Every method here uses targeted SQL queries
that leverage the composite indexes defined in ``models.py``.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Telemetry
from app.repositories.robot_repo import RobotRepository, latest_seen
from app.schemas import TelemetryCreate

# Postgres takes at most 32,767 parameters per statement, and a reading has
# 16 columns. One ingest batch can hold far more readings than that (200
# messages of up to 100 readings each), so inserts go in chunks.
MAX_ROWS_PER_INSERT = 1000

# Executed with the rows as parameters (``executemany``), not built with
# ``.values(rows)``: SQLAlchemy compiles this once and caches it, where a
# multi-row VALUES statement was compiled afresh on every batch, which took
# about 85 ms of CPU for 200 readings against 13 ms this way.
INSERT_READINGS = (
    pg_insert(Telemetry)
    .on_conflict_do_nothing(index_elements=["robot_id", "timestamp"])
    .returning(Telemetry)
)


async def persist(
    db: AsyncSession, rows: list[tuple[TelemetryCreate, datetime]]
) -> list[Telemetry]:
    """The one write path for telemetry: insert, register robots, update state.

    Used by direct ingest, the deferred-write flush and the worker, so all
    three skip duplicates and register robots the same way. Both statements
    run in the caller's transaction; the caller commits.
    """
    inserted = await TelemetryRepository(db).insert_many(rows)
    robots = RobotRepository(db)
    await robots.mark_seen(latest_seen((d.robot_id, ts) for d, ts in rows))
    await robots.upsert_state(_latest_readings(rows))
    return inserted


def _latest_readings(rows: list[tuple[TelemetryCreate, datetime]]) -> list[dict]:
    """The newest reading per robot, as robot_state values."""
    newest: dict[int, tuple[TelemetryCreate, datetime]] = {}
    for data, ts in rows:
        if data.robot_id not in newest or ts > newest[data.robot_id][1]:
            newest[data.robot_id] = (data, ts)
    return [
        {**data.model_dump(exclude={"timestamp"}), "timestamp": ts} for data, ts in newest.values()
    ]


_TREND_FIELDS = (
    "battery",
    "temperature",
    "battery_health",
    "motor_health",
    "sensor_health",
    "network_health",
)


def _trend_keyframe(row: Telemetry) -> dict:
    return {
        "timestamp": row.timestamp.timestamp(),
        "battery": row.battery,
        "battery_rate": row.battery_rate,
        "temperature": row.temperature,
        "speed": row.speed,
        # Robots that report no component health count as healthy, as in
        # fleet status.
        "battery_health": 100.0 if row.battery_health is None else row.battery_health,
        "motor_health": 100.0 if row.motor_health is None else row.motor_health,
        "sensor_health": 100.0 if row.sensor_health is None else row.sensor_health,
        "network_health": 100.0 if row.network_health is None else row.network_health,
    }


class TelemetryRepository:
    """Encapsulates all telemetry-table database operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Write ───────────────────────────────────────────────────────

    async def insert_many(self, rows: list[tuple[TelemetryCreate, datetime]]) -> list[Telemetry]:
        """Insert readings, skipping ones already stored.

        One statement per ``MAX_ROWS_PER_INSERT`` readings, all in the
        caller's transaction.

        A reading is identified by (robot_id, timestamp), enforced by the
        unique ix_telemetry_robot_timestamp index, so a retried upload or a
        replayed worker batch cannot create duplicate rows. Returns only the
        rows that were actually inserted. Does not commit.
        """
        if not rows:
            return []
        values = [{**data.model_dump(exclude={"timestamp"}), "timestamp": ts} for data, ts in rows]
        inserted: list[Telemetry] = []
        for start in range(0, len(values), MAX_ROWS_PER_INSERT):
            chunk = values[start : start + MAX_ROWS_PER_INSERT]
            inserted.extend((await self.db.scalars(INSERT_READINGS, chunk)).all())
        return inserted

    # ── Read ────────────────────────────────────────────────────────

    async def keyframes_between(
        self, robot_id: int, start: datetime, end: datetime
    ) -> list[Telemetry]:
        """The keyframe in force at ``start`` and every one up to ``end``, oldest first."""
        before = (
            select(Telemetry)
            .where(Telemetry.robot_id == robot_id, Telemetry.timestamp <= start)
            .order_by(Telemetry.timestamp.desc())
            .limit(1)
        )
        within = (
            select(Telemetry)
            .where(
                Telemetry.robot_id == robot_id,
                Telemetry.timestamp > start,
                Telemetry.timestamp <= end,
            )
            .order_by(Telemetry.timestamp)
        )
        first = (await self.db.execute(before)).scalars().all()
        rest = (await self.db.execute(within)).scalars().all()
        return [*first, *rest]

    async def get_recent(self, limit: int = 50, skip: int = 0) -> list[Telemetry]:
        """Return the *limit* most recent telemetry rows (all robots)."""
        stmt = select(Telemetry).order_by(Telemetry.timestamp.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_fleet_health_trend(self, limit_minutes: int = 20) -> list[dict]:
        """Fleet health per minute over the last ``limit_minutes``, oldest first.

        Exact and time-weighted (``app/storage/rollups.py``): keyframes are
        unevenly spaced, so ``AVG()`` over rows would let a minute with many
        keyframes from one robot outweigh the rest. Computed from keyframes so
        the current, still filling minute is included; the stored rollups are
        for history.
        """
        from app.storage.rollups import minute_buckets

        now = datetime.now(timezone.utc).timestamp()
        start = (now // 60 - (limit_minutes - 1)) * 60
        stmt = (
            select(Telemetry)
            .where(Telemetry.timestamp >= datetime.fromtimestamp(start - 60, tz=timezone.utc))
            .order_by(Telemetry.robot_id, Telemetry.timestamp)
        )
        by_robot: dict[int, list[dict]] = {}
        for row in (await self.db.execute(stmt)).scalars():
            by_robot.setdefault(row.robot_id, []).append(_trend_keyframe(row))

        totals: dict[float, dict] = {}
        for frames in by_robot.values():
            for bucket_start, bucket in minute_buckets(frames, start, now, 30.0).items():
                row = bucket.row(0)
                acc = totals.setdefault(bucket_start, {"w": 0.0, "n": 0, "sums": {}})
                weight = row["seconds_covered"]
                if weight == 0:
                    # Only an instant so far: the keyframe's own values.
                    frame = [f for f in frames if bucket_start <= f["timestamp"]][0]
                    row = {f"{k}_avg": frame.get(k) for k in _TREND_FIELDS}
                    weight = 1e-6
                acc["w"] += weight
                for k in _TREND_FIELDS:
                    value = row.get(f"{k}_avg")
                    if value is not None:
                        acc["sums"][k] = acc["sums"].get(k, 0.0) + value * weight

        trend = []
        for bucket_start in sorted(totals):
            acc = totals[bucket_start]
            avg = {k: acc["sums"].get(k, 0.0) / acc["w"] for k in _TREND_FIELDS}
            component = (
                avg["battery_health"]
                + avg["motor_health"]
                + avg["sensor_health"]
                + avg["network_health"]
            ) / 4.0
            score = (
                component * 0.55 + avg["battery"] * 0.30 - max(0.0, avg["temperature"] - 55.0) * 1.1
            )
            stamp = datetime.fromtimestamp(bucket_start, tz=timezone.utc)
            trend.append(
                {
                    "timestamp": stamp.isoformat().replace("+00:00", "Z"),
                    "health_score": round(max(0.0, min(100.0, score)), 1),
                }
            )
        return trend

    async def get_mission_completions(self) -> list[dict]:
        """Returns count of completed missions by type in the last 24 hours."""
        stmt = (
            select(
                Telemetry.mission_type,
                func.count(func.distinct(Telemetry.mission_id)).label("count"),
            )
            .filter(Telemetry.timestamp > func.now() - text("INTERVAL '1 day'"))
            .filter(Telemetry.mission_progress >= 100.0)
            .filter(Telemetry.mission_type.isnot(None))
            .group_by(Telemetry.mission_type)
        )
        result = await self.db.execute(stmt)

        mission_types = ["PATROL", "DELIVERY", "INSPECTION"]
        counts = dict.fromkeys(mission_types, 0)
        for row in result.all():
            if row.mission_type in counts:
                counts[row.mission_type] = row.count

        return [{"mission_type": k, "count": v} for k, v in counts.items()]
