"""Expire commands that ran past their deadline.

Runs inside the API process every few seconds. When several API processes
share the database, a transaction-scoped advisory lock lets one of them scan
per round; correctness does not depend on it, because each transition is a
compare-and-set that only one writer can win.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import RobotCommand
from app.services.command_service import TERMINAL_STATES
from app.websocket_manager import manager

logger = logging.getLogger(__name__)

# Arbitrary constant naming this job's advisory lock.
_LOCK_KEY = 4_810_017


async def scan_for_timeouts(session: AsyncSession):
    """Expire commands that ran past their deadline.

    The selection and the write are separated by real time, so a robot can
    acknowledge completion after a command is picked up here but before it is
    written. An unguarded
    ``UPDATE ... WHERE id = :id`` would then overwrite a genuinely COMPLETED
    command with TIMEOUT and broadcast that lie to every dashboard.

    So the terminal-state check is repeated in the ``UPDATE`` itself, making the
    transition a compare-and-set: the database decides, and a row that reached a
    terminal state under us simply matches nothing. This is the same rule
    ``CommandService`` already applies to every other transition; the scanner
    was the one writer not following it.
    """
    now = datetime.now(timezone.utc)

    stmt = select(RobotCommand).where(
        RobotCommand.expires_at < now,
        RobotCommand.status.not_in(TERMINAL_STATES),
    )

    result = await session.execute(stmt)
    expired_commands = result.scalars().all()

    timed_out = []
    for cmd in expired_commands:
        update_stmt = (
            update(RobotCommand)
            .where(
                RobotCommand.id == cmd.id,
                # Re-checked at write time, not just at selection time.
                RobotCommand.status.not_in(TERMINAL_STATES),
            )
            .values(
                status="TIMEOUT",
                completed_at=func.now(),
                error_code="TIMEOUT",
                error_message=f"Command did not complete within {cmd.timeout_seconds} seconds",
            )
        )
        update_result = await session.execute(update_stmt)

        if update_result.rowcount == 0:
            # Finished while the scan was in flight. Not an error - the robot
            # won the race, which is the outcome we want.
            logger.debug("Command %s reached a terminal state during the scan.", cmd.id)
            continue

        logger.info("Command %s timed out. Status updated to TIMEOUT.", cmd.id)
        timed_out.append(cmd)

    # Commit before announcing: a broadcast is not retractable, so nothing is
    # published until the transition it describes is durable.
    if timed_out:
        await session.commit()

    for cmd in timed_out:
        payload = {
            "type": "COMMAND_UPDATE",
            "robot_id": cmd.robot_id,
            "command_type": cmd.command_type,
            "status": "TIMEOUT",
            "command_id": cmd.id,
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        }
        await manager.broadcast(payload, stream="event_stream")


async def run_once(session_factory=AsyncSessionLocal) -> bool:
    """One scan round. Returns False if another process holds the lock."""
    async with session_factory() as session:
        locked = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
        )
        if not locked:
            return False
        await scan_for_timeouts(session)
        return True


async def timeout_loop(interval_seconds: float = 5.0) -> None:
    """Scan for expired commands forever."""
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in command timeout scan")
        await asyncio.sleep(interval_seconds)
