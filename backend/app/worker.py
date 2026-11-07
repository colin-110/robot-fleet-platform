import asyncio
import logging
import signal
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.logging_config import configure_logging
from app.models import RobotCommand
from app.services.command_service import TERMINAL_STATES
from app.websocket_manager import manager

configure_logging(service="worker")
logger = logging.getLogger(__name__)

settings = get_settings()


async def scan_for_timeouts(session: AsyncSession):
    """Expire commands that ran past their deadline.

    The selection and the write are separated by real time - the loop awaits a
    Redis round trip per command - so a robot can acknowledge completion after
    a command is picked up here but before it is written. An unguarded
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


async def timeout_worker_loop():
    logger.info("Starting timeout worker loop")

    # Initialize redis listener for broadcast capability (handled in main or manager, but we'll do it here if it wasn't done)
    if manager._listener_task is None:
        manager._listener_task = asyncio.create_task(manager.listen_to_redis())

    try:
        while True:
            try:
                lock_acquired = await manager.redis.set("lock:timeout_scanner", "1", nx=True, ex=10)
                if lock_acquired:
                    try:
                        async with AsyncSessionLocal() as session:
                            await scan_for_timeouts(session)
                    finally:
                        # Released on completion rather than left to expire.
                        # A 10s TTL against a 5s loop meant every other tick
                        # found the lock still held, so the scan ran at half
                        # the intended rate. The TTL stays as the crash guard.
                        await manager.redis.delete("lock:timeout_scanner")
            except Exception as e:
                logger.error(f"Error in timeout scanner: {e}", exc_info=True)

            await asyncio.sleep(5.0)  # Scan every 5 seconds
    except asyncio.CancelledError:
        logger.info("Timeout scanner task cancelled. Exiting.")
        if manager._listener_task:
            manager._listener_task.cancel()


HEALTH_FILE = Path("/tmp/worker_healthy")


async def health_heartbeat():
    """Periodically touch a health file so Docker can monitor worker liveness."""
    logger.info("Starting worker health heartbeat...")
    while True:
        try:
            HEALTH_FILE.write_text(datetime.now(timezone.utc).isoformat())
        except Exception:
            pass
        await asyncio.sleep(10)


async def main():
    logger.info("Initializing standalone telemetry background processor...")
    # Add a small delay to allow database/redis to finish booting in docker
    await asyncio.sleep(2.0)

    # Run the sync worker, timeout scanner and health heartbeat concurrently
    tasks = [
        asyncio.create_task(timeout_worker_loop()),
        asyncio.create_task(health_heartbeat()),
    ]
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down background processor.")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        # Clean up health file on exit
        HEALTH_FILE.unlink(missing_ok=True)


def handle_sigterm(signum, frame):
    logger.info("Received SIGTERM, shutting down gracefully...")
    raise KeyboardInterrupt()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped.")
