"""
Robot repository - the roster of units that should exist.

Separating "which robots exist" from "which robots recently sent telemetry" is
what makes absence detectable. A fleet list derived only from recent telemetry
cannot distinguish a healthy robot from one that died an hour ago, because both
produce the same thing: no rows.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Robot, RobotState

# Weight of the newest reading in the smoothed drain rate.
DRAIN_ALPHA = 0.2
# A rise larger than this (in battery percent) means the robot is charging.
CHARGE_THRESHOLD = 1.0

READING_FIELDS = (
    "timestamp",
    "battery",
    "temperature",
    "speed",
    "status",
    "mission_id",
    "mission_type",
    "mission_progress",
    "mission_start_time",
    "battery_health",
    "motor_health",
    "sensor_health",
    "network_health",
    "x",
    "y",
)


def latest_seen(readings: Iterable[tuple[int, datetime]]) -> dict[int, datetime]:
    """Newest timestamp per robot from ``(robot_id, timestamp)`` pairs."""
    latest: dict[int, datetime] = {}
    for robot_id, ts in readings:
        if robot_id not in latest or ts > latest[robot_id]:
            latest[robot_id] = ts
    return latest


class RobotRepository:
    """Encapsulates roster reads and the self-registration upsert."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_with_state(self, limit: int, skip: int) -> list[tuple[Robot, RobotState | None]]:
        """Active robots, lowest id first, each with its latest state if any."""
        stmt = (
            select(Robot, RobotState)
            .outerjoin(RobotState, RobotState.robot_id == Robot.id)
            .where(Robot.is_active.is_(True))
            .order_by(Robot.id)
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [(row.Robot, row.RobotState) for row in result.all()]

    async def recent_states(self, within_seconds: float) -> list[RobotState]:
        """States of robots that reported within the last ``within_seconds``."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=within_seconds)
        stmt = select(RobotState).where(RobotState.timestamp > cutoff)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def upsert_state(self, latest: list[dict]) -> None:
        """Replace each robot's state with a newer reading.

        ``latest`` holds one reading dict per robot (with ``robot_id``). An
        older or equal reading leaves the row alone. The drain rate is an
        exponentially weighted average of the battery drop per minute between
        consecutive states, reset to NULL when the battery starts charging.
        """
        if not latest:
            return
        state = RobotState.__table__.c
        stmt = pg_insert(RobotState).values(latest)
        new = stmt.excluded
        minutes = func.extract("epoch", new.timestamp - state.timestamp) / 60.0
        instant = (state.battery - new.battery) / minutes
        drain = case(
            (new.battery > state.battery + CHARGE_THRESHOLD, None),
            else_=DRAIN_ALPHA * instant
            + (1 - DRAIN_ALPHA) * func.coalesce(state.battery_drain_per_min, instant),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[RobotState.robot_id],
            set_={
                **{field: new[field] for field in READING_FIELDS},
                "battery_drain_per_min": drain,
            },
            where=new.timestamp > state.timestamp,
        )
        await self.db.execute(stmt)

    async def list_active(self) -> list[Robot]:
        """Every robot that has not been decommissioned, lowest id first."""
        stmt = select(Robot).where(Robot.is_active.is_(True)).order_by(Robot.id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def mark_seen(self, last_seen_by_robot: dict[int, datetime]) -> None:
        """Register robots and advance their ``last_seen`` in one statement.

        Called by ``persist`` once per batch, in the same transaction as the
        readings.

        ``last_seen`` only moves forward. Redelivered and store-and-forward
        readings can arrive out of order, and a late batch of older readings
        must not rewind a robot's last-seen time. ``GREATEST`` ignores
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

    async def set_key_hash(self, robot_id: int, key_hash: str) -> None:
        """Store a new device key hash, registering the robot if needed."""
        stmt = pg_insert(Robot).values(id=robot_id, is_active=True, key_hash=key_hash)
        stmt = stmt.on_conflict_do_update(index_elements=[Robot.id], set_={"key_hash": key_hash})
        await self.db.execute(stmt)

    async def revoke_key(self, robot_id: int) -> bool:
        """Clear the device key hash. Returns False if the robot is unknown."""
        stmt = update(Robot).where(Robot.id == robot_id).values(key_hash=None).returning(Robot.id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None
