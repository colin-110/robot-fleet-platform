"""
Robot repository — the roster of units that should exist.

Separating "which robots exist" from "which robots recently sent telemetry" is
what makes absence detectable. A fleet list derived only from recent telemetry
cannot distinguish a healthy robot from one that died an hour ago, because both
produce the same thing: no rows.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Robot


class RobotRepository:
    """Encapsulates roster reads and the self-registration upsert."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_active(self) -> list[Robot]:
        """Every robot that has not been decommissioned, lowest id first."""
        stmt = select(Robot).where(Robot.is_active.is_(True)).order_by(Robot.id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def mark_seen(self, last_seen_by_robot: dict[int, datetime]) -> None:
        """Register robots and advance their ``last_seen`` in one statement.

        Called by the worker as it persists each telemetry batch, not from the
        request path — the whole point of the Redis buffer is that an ingest
        request does no database work, and a per-request roster upsert would
        put it straight back.

        ``last_seen`` only moves forward. A worker restart replays its pending
        list, so batches can be drained out of order, and a late batch of older
        readings must not rewind a robot's last-seen time. ``GREATEST`` ignores
        NULLs but returns NULL if *every* argument is NULL, hence the COALESCE
        for a robot being registered for the first time.
        """
        if not last_seen_by_robot:
            return

        rows = [
            {"id": robot_id, "is_active": True, "last_seen": seen_at}
            for robot_id, seen_at in last_seen_by_robot.items()
        ]

        stmt = pg_insert(Robot).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Robot.id],
            set_={
                "last_seen": func.greatest(
                    stmt.excluded.last_seen,
                    func.coalesce(Robot.__table__.c.last_seen, stmt.excluded.last_seen),
                )
            },
        )
        await self.db.execute(stmt)
