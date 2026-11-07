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


class TelemetryRepository:
    """Encapsulates all telemetry-table database operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Write ───────────────────────────────────────────────────────

    async def insert_many(self, rows: list[tuple[TelemetryCreate, datetime]]) -> list[Telemetry]:
        """Insert readings in one statement, skipping ones already stored.

        A reading is identified by (robot_id, timestamp), enforced by the
        unique ix_telemetry_robot_timestamp index, so a retried upload or a
        replayed worker batch cannot create duplicate rows. Returns only the
        rows that were actually inserted. Does not commit.
        """
        if not rows:
            return []
        values = [{**data.model_dump(exclude={"timestamp"}), "timestamp": ts} for data, ts in rows]
        stmt = (
            pg_insert(Telemetry)
            .values(values)
            .on_conflict_do_nothing(index_elements=["robot_id", "timestamp"])
            .returning(Telemetry)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── Read ────────────────────────────────────────────────────────

    async def get_recent(self, limit: int = 50, skip: int = 0) -> list[Telemetry]:
        """Return the *limit* most recent telemetry rows (all robots)."""
        stmt = select(Telemetry).order_by(Telemetry.id.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_fleet_health_trend(self, limit_minutes: int = 20) -> list[dict]:
        """Calculates fleet health trend over the last limit_minutes by grouping by minute."""
        stmt = (
            select(
                func.date_trunc("minute", Telemetry.timestamp).label("bucket"),
                func.avg(Telemetry.battery).label("avg_battery"),
                func.avg(Telemetry.temperature).label("avg_temperature"),
                func.avg(Telemetry.battery_health).label("avg_batt_h"),
                func.avg(Telemetry.motor_health).label("avg_motor_h"),
                func.avg(Telemetry.sensor_health).label("avg_sensor_h"),
                func.avg(Telemetry.network_health).label("avg_net_h"),
            )
            .where(Telemetry.timestamp > func.now() - text(f"INTERVAL '{limit_minutes} minutes'"))
            .group_by("bucket")
            .order_by(text("bucket DESC"))
            .limit(limit_minutes)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        trend = []
        for row in reversed(rows):  # oldest first
            avg_component = (
                row.avg_batt_h + row.avg_motor_h + row.avg_sensor_h + row.avg_net_h
            ) / 4.0
            score = (
                (avg_component * 0.55)
                + (row.avg_battery * 0.30)
                - max(0.0, row.avg_temperature - 55.0) * 1.1
            )
            score = max(0.0, min(100.0, score))

            # format bucket properly
            bucket = row.bucket
            if bucket.tzinfo is None:
                bucket = bucket.replace(tzinfo=timezone.utc)
            else:
                bucket = bucket.astimezone(timezone.utc)

            trend.append(
                {
                    "timestamp": bucket.isoformat().replace("+00:00", "Z"),
                    "health_score": round(score, 1),
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
