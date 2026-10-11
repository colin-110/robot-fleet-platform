"""
Telemetry repository — all database queries in one place.

This replaces the pattern of loading the *entire* telemetry table into
memory on every request.  Every method here uses targeted SQL queries
that leverage the composite indexes defined in ``models.py``.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Telemetry
from app.schemas import TelemetryCreate


def insert_telemetry_ignoring_duplicates(rows: list[dict]):
    """``INSERT ... ON CONFLICT (robot_id, timestamp) DO NOTHING`` for ``rows``.

    The single write statement for telemetry. Every path that persists
    readings (direct ingest, the deferred buffer, the stream worker) goes
    through it, so each one is safe to retry: a reading already stored is
    dropped, not duplicated. Duplicates within one batch are dropped too.
    """
    return (
        pg_insert(Telemetry)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[Telemetry.robot_id, Telemetry.timestamp])
    )


def _to_row(data: TelemetryCreate, timestamp: datetime) -> dict:
    return {
        "robot_id": data.robot_id,
        "battery": data.battery,
        "temperature": data.temperature,
        "speed": data.speed,
        "status": data.status,
        "mission_id": data.mission_id,
        "mission_type": data.mission_type,
        "mission_progress": data.mission_progress,
        "mission_start_time": data.mission_start_time,
        "battery_health": data.battery_health,
        "motor_health": data.motor_health,
        "sensor_health": data.sensor_health,
        "network_health": data.network_health,
        "x": data.x,
        "y": data.y,
        "timestamp": timestamp,
    }


class TelemetryRepository:
    """Encapsulates all telemetry-table database operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Write ───────────────────────────────────────────────────────

    async def insert(
        self, data: TelemetryCreate, timestamp: datetime | None = None
    ) -> Telemetry | None:
        """Persist a single telemetry reading.

        ``timestamp`` is the already-resolved measurement time (device time
        where reported, server time otherwise). The caller resolves it so that
        clock-skew handling lives in one place rather than being duplicated
        across the buffered and direct write paths.

        Returns ``None`` when the reading is already stored — see
        ``insert_many``. Does not commit.
        """
        rows = await self.insert_many([(data, timestamp or datetime.now(timezone.utc))])
        return rows[0] if rows else None

    async def insert_many(self, rows: list[tuple[TelemetryCreate, datetime]]) -> list[Telemetry]:
        """Persist many telemetry readings in one statement, skipping duplicates.

        One multi-row INSERT rather than a commit per row: committing per row
        turned a single simulator POST into up to 50 sequential round trips to
        a remote Postgres instance, slow enough to blow past the simulator's
        own POST timeout and drop the whole batch.

        Returns only the rows actually inserted. A reading whose
        ``(robot_id, timestamp)`` is already stored is a retry of one we have,
        and is skipped rather than duplicated. Does not commit: the caller
        commits, so the roster update lands in the same transaction.
        """
        if not rows:
            return []

        stmt = insert_telemetry_ignoring_duplicates(
            [_to_row(data, timestamp) for data, timestamp in rows]
        ).returning(Telemetry)
        result = await self.db.scalars(stmt, execution_options={"populate_existing": True})
        return list(result.all())

    async def get_id(self, robot_id: int, timestamp: datetime) -> int | None:
        """Id of the stored reading for ``(robot_id, timestamp)``, if any."""
        stmt = select(Telemetry.id).where(
            Telemetry.robot_id == robot_id, Telemetry.timestamp == timestamp
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ── Read ────────────────────────────────────────────────────────

    async def get_recent(self, limit: int = 50, skip: int = 0) -> list[Telemetry]:
        """Return the *limit* most recent telemetry rows (all robots)."""
        stmt = select(Telemetry).order_by(Telemetry.id.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_per_robot(
        self, per_robot_limit: int = 30, window_minutes: int = 15
    ) -> dict[int, list[Telemetry]]:
        """
        Return the most recent *per_robot_limit* rows **per robot**.

        Only rows within the last *window_minutes* are considered.  This keeps
        the query bounded to a small, index-friendly tail of the table instead
        of scanning/sorting the entire telemetry history, which grows without
        bound while the fleet is running.  Robots that have not reported within
        the window are treated as gone from the current fleet.
        """
        # Use a window function to avoid N+1 queries.  The WHERE clause limits
        # the scan to the recent tail of the table (uses the timestamp index),
        # so the query cost stays flat as history grows.
        subq = (
            select(
                Telemetry,
                func.row_number()
                .over(partition_by=Telemetry.robot_id, order_by=Telemetry.timestamp.desc())
                .label("rn"),
            )
            .where(
                Telemetry.timestamp > func.now() - text(f"INTERVAL '{int(window_minutes)} minutes'")
            )
            .subquery()
        )

        telemetry_alias = aliased(Telemetry, subq)
        stmt = (
            select(telemetry_alias)
            .where(subq.c.rn <= per_robot_limit)
            .order_by(telemetry_alias.robot_id, telemetry_alias.timestamp.desc())
        )

        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        grouped: dict[int, list[Telemetry]] = {}
        for row in rows:
            grouped.setdefault(row.robot_id, []).append(row)

        for rid in grouped:
            grouped[rid] = list(reversed(grouped[rid]))  # oldest first

        return grouped

    # ── Analytics ───────────────────────────────────────────────────

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
