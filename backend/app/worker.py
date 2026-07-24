import asyncio
import orjson
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import socket
import signal

from sqlalchemy import insert, delete, update, func
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Telemetry, RobotCommand
from app.websocket_manager import manager

# Configure logging for worker
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
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


async def process_batch(session: AsyncSession, stream_messages: list) -> list:
    """Parse a batch of Redis-stream telemetry entries and bulk-insert them.

    ``stream_messages`` is a list of ``(msg_id, fields)`` tuples as returned by
    ``XREADGROUP``, where ``fields`` carries a JSON ``"payload"``. Non-telemetry
    payloads (events/commands — anything without a ``battery`` field) and
    undecodable payloads are skipped, but every message id seen is returned so
    the caller can ``XACK`` the whole batch and avoid reprocessing.
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
        await session.execute(insert(Telemetry).values(insert_dicts))
        await session.commit()
        logger.info(
            "Successfully batched and saved %d telemetry records to database.",
            len(insert_dicts),
        )

    return message_ids


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
                CONSUMER_GROUP, CONSUMER_NAME,
                {STREAM_KEY: "0"},
                count=100
            )

            if not messages or not messages[0][1]:
                try:
                    res = await redis.execute_command("XAUTOCLAIM", STREAM_KEY, CONSUMER_GROUP, CONSUMER_NAME, "60000", "0", "COUNT", "100")
                    if res and len(res) >= 2 and res[1]:
                        continue
                except Exception as e:
                    logger.warning("XAUTOCLAIM failed: %s", e)

                messages = await redis.xreadgroup(
                    CONSUMER_GROUP, CONSUMER_NAME,
                    {STREAM_KEY: ">"},
                    count=100, block=500
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
            logger.error("Error in database sync worker: %s. Retrying in %.1fs...", e, retry_delay, exc_info=True)
            await asyncio.sleep(retry_delay)
            retry_delay = min(max_retry_delay, retry_delay * 2.0)


async def db_pruner_task():
    """Daily database telemetry pruner to maintain bounded disk footprint."""
    logger.info("Starting database retention pruner (retention=%d days)...", settings.retention_days)
    
    while True:
        try:
            lock_acquired = await manager.redis.set("lock:db_pruner", "1", nx=True, ex=3600)
            if lock_acquired:
                limit_date = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
                
                total_deleted = 0
                while True:
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            # Use a subquery to find IDs to delete, with a limit to batch it
                            subq = select(Telemetry.id).where(Telemetry.timestamp < limit_date).limit(10000).subquery()
                            stmt = delete(Telemetry).where(Telemetry.id.in_(select(subq)))
                            result = await session.execute(stmt)
                            
                            deleted_count = result.rowcount
                            total_deleted += deleted_count
                            
                    if deleted_count == 0:
                        break
                    await asyncio.sleep(0.1) # Yield to avoid locking DB too hard
                        
                logger.info("RETENTION: Pruned %d telemetry records older than %d days.", total_deleted, settings.retention_days)
            
            # Run once every 24 hours
            await asyncio.sleep(86400)
            
        except asyncio.CancelledError:
            logger.info("Retention pruner task cancelled. Exiting.")
            break
        except Exception as e:
            logger.error("Error in retention pruner task: %s. Retrying in 1 hour...", e)
            await asyncio.sleep(3600)


async def scan_for_timeouts(session: AsyncSession):
    """Scan for commands that have expired and mark them as TIMEOUT."""
    now = datetime.now(timezone.utc)
        
    # Find commands where expires_at < now and status not in terminal states
    stmt = select(RobotCommand).where(
        RobotCommand.expires_at < now,
        RobotCommand.status.not_in(["COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"])
    )
    
    result = await session.execute(stmt)
    expired_commands = result.scalars().all()
    
    for cmd in expired_commands:
        logger.info(f"Command {cmd.id} timed out. Updating status to TIMEOUT.")
        
        # Update to TIMEOUT
        update_stmt = (
            update(RobotCommand)
            .where(RobotCommand.id == cmd.id)
            .values(
                status="TIMEOUT",
                completed_at=func.now(),
                error_code="TIMEOUT",
                error_message=f"Command did not complete within {cmd.timeout_seconds} seconds"
            )
        )
        await session.execute(update_stmt)
        
        # Broadcast the timeout so the frontend and simulator know
        payload = {
            "type": "COMMAND_UPDATE",
            "robot_id": cmd.robot_id,
            "command_type": cmd.command_type,
            "status": "TIMEOUT",
            "command_id": cmd.id,
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        }
        await manager.broadcast(payload, stream="event_stream")
        
    if expired_commands:
        await session.commit()

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
                    async with AsyncSessionLocal() as session:
                        await scan_for_timeouts(session)
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
