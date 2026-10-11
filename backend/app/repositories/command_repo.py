"""
Command repository — all database queries for robot commands.

Encapsulates all command-table database operations, following the
same pattern as TelemetryRepository.
"""

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models import RobotCommand


class CommandRepository:
    """Encapsulates all robot-command database operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Read ────────────────────────────────────────────────────────

    async def get_next_pending(self, robot_id: int) -> RobotCommand | None:
        """Get the oldest PENDING command for a robot (FIFO dispatch)."""
        stmt = (
            select(RobotCommand)
            .where(
                RobotCommand.robot_id == robot_id,
                RobotCommand.status == "PENDING",
            )
            .order_by(RobotCommand.created_at.asc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_unacknowledged(
        self, robot_id: int, dispatched_before: datetime
    ) -> list[RobotCommand]:
        """DISPATCHED commands the robot never acknowledged, oldest first.

        Dispatched before ``dispatched_before``, so a command claimed a moment
        ago, which the robot is still working through, is not handed out twice.
        """
        stmt = (
            select(RobotCommand)
            .where(
                RobotCommand.robot_id == robot_id,
                RobotCommand.status == "DISPATCHED",
                RobotCommand.dispatched_at < dispatched_before,
            )
            .order_by(RobotCommand.created_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── Atomic Dispatch ─────────────────────────────────────────────

    async def try_dispatch(self, command_id: str, now: datetime | None = None) -> bool:
        """
        Atomically update a PENDING command to DISPATCHED.

        Returns True if the update succeeded (we won the race),
        False if another process already grabbed it.
        """
        now = now or datetime.now(timezone.utc)
        stmt = (
            update(RobotCommand)
            .where(
                RobotCommand.id == command_id,
                RobotCommand.status == "PENDING",
            )
            .values(status="DISPATCHED", dispatched_at=now)
            .execution_options(synchronize_session=False)
        )
        result = await self.db.execute(stmt)
        if result.rowcount > 0:
            await self.db.commit()
            return True
        else:
            await self.db.rollback()
            return False
