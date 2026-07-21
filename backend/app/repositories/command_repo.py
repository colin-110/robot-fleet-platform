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

    # ── Write ───────────────────────────────────────────────────────

    async def insert(self, command: RobotCommand) -> RobotCommand:
        """Persist a new command and return it."""
        self.db.add(command)
        await self.db.commit()
        await self.db.refresh(command)
        return command

    async def save(self, command: RobotCommand) -> None:
        """Commit pending changes on an existing command."""
        await self.db.commit()
        await self.db.refresh(command)

    # ── Read ────────────────────────────────────────────────────────

    async def get_by_id(self, command_id: str) -> RobotCommand | None:
        """Fetch a single command by its ID."""
        stmt = select(RobotCommand).where(RobotCommand.id == command_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_by_idempotency_key(
        self, robot_id: int, idempotency_key: str
    ) -> RobotCommand | None:
        """Fetch a command by robot_id + idempotency_key."""
        stmt = select(RobotCommand).where(
            RobotCommand.robot_id == robot_id,
            RobotCommand.idempotency_key == idempotency_key,
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

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

    # ── Atomic Dispatch ─────────────────────────────────────────────

    async def try_dispatch(
        self, command_id: str, now: datetime | None = None
    ) -> bool:
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
