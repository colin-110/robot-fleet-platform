"""
Telemetry repository — all database queries in one place.

This replaces the pattern of loading the *entire* telemetry table into
memory on every request.  Every method here uses targeted SQL queries
that leverage the composite indexes defined in ``models.py``.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.exc import SQLAlchemyError

from app.models import Telemetry
from app.schemas import TelemetryCreate


class TelemetryRepository:
    """Encapsulates all telemetry-table database operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Write ───────────────────────────────────────────────────────

    async def insert(self, data: TelemetryCreate) -> Telemetry:
        """Persist a single telemetry reading and return the ORM row."""
        telemetry = Telemetry(
            robot_id=data.robot_id,
            battery=data.battery,
            temperature=data.temperature,
            speed=data.speed,
            status=data.status,
            mission_id=data.mission_id,
            mission_type=data.mission_type,
            mission_progress=data.mission_progress,
            mission_start_time=data.mission_start_time,
            battery_health=data.battery_health,
            motor_health=data.motor_health,
            sensor_health=data.sensor_health,
            network_health=data.network_health,
            x=data.x,
            y=data.y,
            timestamp=datetime.now(timezone.utc),
        )
        self.db.add(telemetry)
        await self.db.commit()
        await self.db.refresh(telemetry)
        return telemetry

    # ── Read ────────────────────────────────────────────────────────

    async def get_recent(self, limit: int = 50) -> list[Telemetry]:
        """Return the *limit* most recent telemetry rows (all robots)."""
        stmt = select(Telemetry).order_by(Telemetry.id.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_per_robot(self, per_robot_limit: int = 30) -> dict[int, list[Telemetry]]:
        """
        Return the most recent *per_robot_limit* rows **per robot**.

        Uses a SQL window function so we only fetch the rows we need
        instead of loading the full table.

        Returns:
            ``{robot_id: [rows oldest→newest]}``
        """
        subquery = (
            select(
                Telemetry,
                func.row_number()
                .over(
                    partition_by=Telemetry.robot_id,
                    order_by=Telemetry.timestamp.desc(),
                )
                .label("rn"),
            )
            .subquery()
        )

        telemetry_alias = aliased(Telemetry, subquery)
        try:
            stmt = (
                select(telemetry_alias)
                .filter(subquery.c.rn <= per_robot_limit)
                .order_by(telemetry_alias.robot_id, telemetry_alias.timestamp.asc())
            )
            result = await self.db.execute(stmt)
            rows = list(result.scalars().all())
        except SQLAlchemyError:
            rows = []

        # Fallback: if the window-function approach raises on some DB
        # drivers, use the simpler grouped approach.
        if not rows:
            rows = await self._get_recent_per_robot_fallback(per_robot_limit)

        grouped: dict[int, list[Telemetry]] = {}
        for row in rows:
            grouped.setdefault(row.robot_id, []).append(row)
        return grouped

    async def _get_recent_per_robot_fallback(self, per_robot_limit: int) -> list[Telemetry]:
        """
        Fallback that loads recent rows efficiently without window functions.

        Fetches at most ``per_robot_limit * robot_count`` rows from the
        tail of the table, which is a bounded scan.
        """
        # First, find distinct robot IDs (cheap query on indexed column)
        stmt = select(Telemetry.robot_id).distinct()
        result = await self.db.execute(stmt)
        robot_ids = [rid for (rid,) in result.all()]

        all_rows: list[Telemetry] = []
        for rid in robot_ids:
            stmt_rows = (
                select(Telemetry)
                .filter(Telemetry.robot_id == rid)
                .order_by(Telemetry.timestamp.desc())
                .limit(per_robot_limit)
            )
            res = await self.db.execute(stmt_rows)
            rows = list(res.scalars().all())
            all_rows.extend(reversed(rows))  # oldest first
        return all_rows

    async def get_all_ordered(self, limit: int = 5000) -> list[Telemetry]:
        """
        Return up to *limit* rows ordered by timestamp ascending.

        Used by analytics that need time-series data across all robots.
        Capped to prevent runaway memory usage.
        """
        stmt = (
            select(Telemetry)
            .order_by(Telemetry.timestamp.asc(), Telemetry.id.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── Analytics ───────────────────────────────────────────────────

    async def get_fleet_health_trend(self, limit_minutes: int = 20) -> list[dict]:
        """Calculates fleet health trend over the last limit_minutes by grouping by minute."""
        stmt = (
            select(
                func.date_trunc('minute', Telemetry.timestamp).label("bucket"),
                func.avg(Telemetry.battery).label("avg_battery"),
                func.avg(Telemetry.temperature).label("avg_temperature"),
                func.avg(Telemetry.battery_health).label("avg_batt_h"),
                func.avg(Telemetry.motor_health).label("avg_motor_h"),
                func.avg(Telemetry.sensor_health).label("avg_sensor_h"),
                func.avg(Telemetry.network_health).label("avg_net_h"),
            )
            .group_by("bucket")
            .order_by(text("bucket DESC"))
            .limit(limit_minutes)
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        
        trend = []
        for row in reversed(rows): # oldest first
            avg_component = (row.avg_batt_h + row.avg_motor_h + row.avg_sensor_h + row.avg_net_h) / 4.0
            score = (avg_component * 0.55) + (row.avg_battery * 0.30) - max(0.0, row.avg_temperature - 55.0) * 1.1
            score = max(0.0, min(100.0, score))
            
            # format bucket properly
            bucket = row.bucket
            if bucket.tzinfo is None:
                bucket = bucket.replace(tzinfo=timezone.utc)
            else:
                bucket = bucket.astimezone(timezone.utc)
                
            trend.append({
                "timestamp": bucket.isoformat().replace("+00:00", "Z"),
                "health_score": round(score, 1)
            })
        return trend

    async def get_mission_completions(self) -> list[dict]:
        """Returns count of completed missions by type."""
        stmt = (
            select(
                Telemetry.mission_type,
                func.count(func.distinct(Telemetry.mission_id)).label("count")
            )
            .filter(Telemetry.mission_progress >= 100.0)
            .filter(Telemetry.mission_type.isnot(None))
            .group_by(Telemetry.mission_type)
        )
        result = await self.db.execute(stmt)
        
        mission_types = ["PATROL", "DELIVERY", "INSPECTION"]
        counts = {m_type: 0 for m_type in mission_types}
        for row in result.all():
            if row.mission_type in counts:
                counts[row.mission_type] = row.count
                
        return [{"mission_type": k, "count": v} for k, v in counts.items()]
