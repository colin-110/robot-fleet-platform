import asyncio
import logging
import signal
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import orjson
from sqlalchemy import func, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.logging_config import configure_logging
from app.metrics import (
    db_write_latency_seconds,
    telemetry_stream_length,
    telemetry_stream_pending,
    worker_batch_size,
)
from app.models import RobotCommand, Telemetry
from app.repositories.robot_repo import RobotRepository
from app.retention import prune_loop
from app.services.command_service import TERMINAL_STATES
from app.websocket_manager import manager

configure_logging(service="worker")
logger = logging.getLogger(__name__)

settings = get_settings()

STREAM_KEY = "telemetry_stream"
CONSUMER_GROUP = "db_writers"
CONSUMER_NAME = f"worker-{socket.gethostname()}"


def _payload_to_insert_dict(p: dict) -> dict:
    """Normalize a decoded telemetry payload into a Telemetry insert row."""
    ts_raw = p.get("timestamp") or datetime.now(timezone.utc).isoformat()
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
    except ValueError:
        ts = datetime.now(timezone.utc)

    mst_raw = p.get("mission_start_time")
    if isinstance(mst_raw, str):
        try:
            mst = datetime.fromisoformat(mst_raw.replace("Z", "+00:00"))
        except ValueError:
            mst = None
    else:
        mst = None

    return {
        "robot_id": p.get("robot_id", 0),
        "battery": p.get("battery", 0.0),
        "temperature": p.get("temperature", 0.0),
        "speed": p.get("speed", 0.0),
        "status": p.get("status", "UNKNOWN"),
        "mission_id": p.get("mission_id"),
        "mission_type": p.get("mission_type"),
        "mission_progress": p.get("mission_progress"),
        "mission_start_time": mst,
        "battery_health": p.get("battery_health"),
        "motor_health": p.get("motor_health"),
        "sensor_health": p.get("sensor_health"),
        "network_health": p.get("network_health"),
        "x": p.get("x"),
        "y": p.get("y"),
        "timestamp": ts,
    }


async def _register_robots(session: AsyncSession, insert_dicts: list[dict]) -> None:
    """Keep the robot roster in step with the telemetry just persisted.

    Done here rather than on the request path deliberately: the Redis buffer
    exists so an ingest request performs no database work, and a per-request
    roster upsert would put it straight back. Batching it costs one extra
    statement per batch instead of one per reading.

    The roster is what lets the dashboard show a robot that has gone silent.
    Without it the fleet list is just "whoever reported recently", and a dead
    unit disappears instead of being flagged.
    """
    latest_by_robot: dict[int, datetime] = {}
    for row in insert_dicts:
        robot_id = row["robot_id"]
        ts = row["timestamp"]
        if robot_id not in latest_by_robot or ts > latest_by_robot[robot_id]:
            latest_by_robot[robot_id] = ts

    await RobotRepository(session).mark_seen(latest_by_robot)


async def process_batch(session: AsyncSession, stream_messages: list) -> list:
    """Parse a batch of Redis-stream telemetry entries and persist them.

    ``stream_messages`` is a list of ``(msg_id, fields)`` tuples as returned by
    ``XREADGROUP``, where ``fields`` carries a JSON ``"payload"``. Non-telemetry
    payloads (events/commands — anything without a ``battery`` field) and
    undecodable payloads are skipped, but every message id seen is returned so
    the caller can ``XACK`` the whole batch and avoid reprocessing.

    With ``OPT_BATCH_INSERT`` on (default) the whole batch goes out as one
    multi-row INSERT in a single transaction. With it off, each row is its own
    INSERT + COMMIT — the naive approach, and the baseline the benchmark
    compares against. The difference is round trips: one per batch versus one
    per row, each carrying full transaction-commit overhead.
    """
    message_ids: list = []
    insert_dicts: list = []

    for msg_id, fields in stream_messages:
        message_ids.append(msg_id)
        payload_raw = fields.get("payload") if fields else None
        if not payload_raw:
            continue
        try:
            decoded = orjson.loads(payload_raw)
        except orjson.JSONDecodeError:
            logger.warning("Failed to decode Redis payload: %s", payload_raw)
            continue
        # Only telemetry objects carry a battery reading; ignore events/commands.
        if "battery" in decoded:
            insert_dicts.append(_payload_to_insert_dict(decoded))

    if insert_dicts:
        started = time.perf_counter()

        if settings.opt_batch_insert:
            await session.execute(insert(Telemetry).values(insert_dicts))
            await _register_robots(session, insert_dicts)
            await session.commit()
            mode = "batch"
        else:
            for row in insert_dicts:
                await session.execute(insert(Telemetry).values(row))
                await session.commit()
            await _register_robots(session, insert_dicts)
            await session.commit()
            mode = "row_by_row"

        elapsed = time.perf_counter() - started
        db_write_latency_seconds.labels(mode=mode).observe(elapsed)
        worker_batch_size.observe(len(insert_dicts))
        logger.info(
            "Persisted %d telemetry records (%s) in %.1f ms.",
            len(insert_dicts),
            mode,
            elapsed * 1000,
        )

    return message_ids


async def report_stream_depth():
    """Publish stream length and unacknowledged count as metrics.

    The telemetry stream is capped, and Redis trims the oldest entries past the
    cap whether or not a consumer group has acknowledged them. A worker that
    falls far enough behind therefore drops telemetry with no error anywhere.
    Exporting both numbers turns that silent failure into an alertable one:
    pending climbing toward the cap means the worker is losing the race.
    """
    while True:
        try:
            length = await manager.redis.xlen(STREAM_KEY)
            telemetry_stream_length.set(length)

            try:
                pending = await manager.redis.xpending(STREAM_KEY, CONSUMER_GROUP)
                count = pending.get("pending", 0) if isinstance(pending, dict) else 0
                telemetry_stream_pending.set(count)
            except Exception:
                # Group may not exist yet on a cold start.
                telemetry_stream_pending.set(0)

            if length >= settings.telemetry_stream_maxlen * 0.9:
                logger.warning(
                    "Telemetry stream at %d/%d entries — Redis will trim "
                    "unacknowledged telemetry if the worker falls further behind.",
                    length,
                    settings.telemetry_stream_maxlen,
                )
        except asyncio.CancelledError:
            logger.info("Stream depth reporter cancelled. Exiting.")
            break
        except Exception:
            logger.warning("Failed to sample stream depth", exc_info=True)

        await asyncio.sleep(10)


async def redis_to_db_sync_worker():
    """Background worker that batch-saves telemetry from Redis to PostgreSQL."""
    logger.info("Starting Redis-to-DB sync worker...")
    redis = manager.redis

    retry_delay = 2.0  # seconds
    max_retry_delay = 30.0

    # Ensure consumer group exists
    try:
        await redis.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.error("Failed to create consumer group: %s", e)

    while True:
        try:
            message_ids = []

            messages = await redis.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME, {STREAM_KEY: "0"}, count=100
            )

            if not messages or not messages[0][1]:
                try:
                    res = await redis.execute_command(
                        "XAUTOCLAIM",
                        STREAM_KEY,
                        CONSUMER_GROUP,
                        CONSUMER_NAME,
                        "60000",
                        "0",
                        "COUNT",
                        "100",
                    )
                    if res and len(res) >= 2 and res[1]:
                        continue
                except Exception as e:
                    logger.warning("XAUTOCLAIM failed: %s", e)

                messages = await redis.xreadgroup(
                    CONSUMER_GROUP, CONSUMER_NAME, {STREAM_KEY: ">"}, count=100, block=500
                )

            if messages:
                stream_messages = []
                for _stream, stream_msgs in messages:
                    stream_messages.extend(stream_msgs)

                if stream_messages:
                    async with AsyncSessionLocal() as session:
                        message_ids = await process_batch(session, stream_messages)
                    # Reset retry delay on a successful drain of the batch.
                    retry_delay = 2.0

            # Acknowledge messages in Redis stream so they are removed from PEL
            if message_ids:
                await redis.xack(STREAM_KEY, CONSUMER_GROUP, *message_ids)
                # Yield briefly before checking the queue again.
                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            logger.info("Sync worker task cancelled. Exiting.")
            break
        except Exception as e:
            logger.error(
                "Error in database sync worker: %s. Retrying in %.1fs...",
                e,
                retry_delay,
                exc_info=True,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(max_retry_delay, retry_delay * 2.0)


async def db_pruner_task():
    """Daily database telemetry pruner to maintain bounded disk footprint.

    Thin wrapper around app.retention.prune_loop - see that module for the
    actual pruning logic, which the backend also runs on its own so a
    deployment with no separate worker process still prunes.
    """
    logger.info(
        "Starting database retention pruner (retention=%d days)...", settings.retention_days
    )
    try:
        await prune_loop(use_redis_lock=True, initial_delay_seconds=0, interval_seconds=86400)
    except asyncio.CancelledError:
        logger.info("Retention pruner task cancelled. Exiting.")
        raise


async def scan_for_timeouts(session: AsyncSession):
    """Expire commands that ran past their deadline.

    The selection and the write are separated by real time — the loop awaits a
    Redis round trip per command — so a robot can acknowledge completion after
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
            # Finished while the scan was in flight. Not an error — the robot
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

    # Run sync worker, retention pruner, timeout scanner, and health heartbeat concurrently
    tasks = [
        asyncio.create_task(redis_to_db_sync_worker()),
        asyncio.create_task(db_pruner_task()),
        asyncio.create_task(timeout_worker_loop()),
        asyncio.create_task(health_heartbeat()),
        asyncio.create_task(report_stream_depth()),
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
