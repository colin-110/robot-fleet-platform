import asyncio
import orjson
import logging
import sys
from datetime import datetime, timezone, timedelta

from sqlalchemy import insert, delete, update
from sqlalchemy.future import select
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Telemetry, RobotCommand
from app.websocket_manager import manager

# Configure logging for worker
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  [WORKER] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout
)
logger = logging.getLogger("worker")


async def redis_to_db_sync_worker():
    """Background worker that batch-saves telemetry from Redis to PostgreSQL."""
    logger.info("Starting Redis-to-DB sync worker...")
    redis = manager.redis
    
    retry_delay = 2.0  # seconds
    max_retry_delay = 30.0
    
    # Ensure consumer group exists
    try:
        await redis.xgroup_create("telemetry_stream", "telemetry_group", id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.error("Failed to create consumer group: %s", e)
    
    while True:
        try:
            payloads = []
            message_ids = []
            
            # Block for up to 500ms waiting for new messages
            messages = await redis.xreadgroup(
                "telemetry_group", "worker-1", 
                {"telemetry_stream": ">"}, 
                count=100, block=500
            )
            
            if messages:
                for stream, stream_msgs in messages:
                    for msg_id, msg_data in stream_msgs:
                        payload_raw = msg_data.get(b"payload") or msg_data.get("payload")
                        if payload_raw:
                            try:
                                decoded = orjson.loads(payload_raw)
                                # Only process telemetry objects (ignore events/commands)
                                if "battery" in decoded:
                                    payloads.append(decoded)
                                message_ids.append(msg_id)
                            except orjson.JSONDecodeError:
                                logger.warning("Failed to decode Redis payload: %s", payload_raw)
                                message_ids.append(msg_id)
                
            if payloads:
                insert_dicts = []
                for p in payloads:
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
                    
                    insert_dicts.append({
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
                        "timestamp": ts
                    })
                
                # Perform bulk insert
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        stmt = insert(Telemetry).values(insert_dicts)
                        await session.execute(stmt)
                        
                logger.info("Successfully batched and saved %d telemetry records to database.", len(insert_dicts))
                
                # Acknowledge messages in Redis stream so they are removed from PEL
                if message_ids:
                    await redis.xack("telemetry_stream", "telemetry_group", *message_ids)
                    
                # Reset retry delay on successful database write
                retry_delay = 2.0
            
            if not payloads:
                # Loop immediately handles blocking wait next iteration
                pass
            else:
                # Yield briefly before checking queue again
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
            limit_date = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
            
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    stmt = delete(Telemetry).where(Telemetry.timestamp < limit_date)
                    result = await session.execute(stmt)
                    
            logger.info("RETENTION: Pruned %d telemetry records older than %d days.", result.rowcount, settings.retention_days)
            
            # Run once every 24 hours
            await asyncio.sleep(86400)
            
        except asyncio.CancelledError:
            logger.info("Retention pruner task cancelled. Exiting.")
            break
        except Exception as e:
            logger.error("Error in retention pruner task: %s. Retrying in 1 hour...", e)
            await asyncio.sleep(3600)


async def scan_for_timeouts():
    """Scan for commands that have expired and mark them as TIMEOUT."""
    async with AsyncSessionLocal() as session:
        now = datetime.now(timezone.utc)
        
        # Find commands where expires_at < now and status not in terminal states
        stmt = select(RobotCommand).where(
            RobotCommand.expires_at < now,
            RobotCommand.status.not_in(["COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"])
        )
        
        result = await session.execute(stmt)
        expired_commands = result.scalars().all()
        
        for cmd in expired_commands:
            logger.info(f"Command {cmd.id} for robot {cmd.robot_id} has expired (status was {cmd.status})")
            
            # Update to TIMEOUT
            update_stmt = (
                update(RobotCommand)
                .where(RobotCommand.id == cmd.id, RobotCommand.status == cmd.status)
                .values(
                    status="TIMEOUT",
                    completed_at=now,
                    error_code="TIMEOUT",
                    error_message=f"Command timed out before completion (was {cmd.status})"
                )
                .execution_options(synchronize_session=False)
            )
            update_result = await session.execute(update_stmt)
            
            if update_result.rowcount > 0:
                await session.commit()
                
                # Broadcast
                payload = {
                    "type": "COMMAND_UPDATE",
                    "robot_id": cmd.robot_id,
                    "command_type": cmd.command_type,
                    "status": "TIMEOUT",
                    "command_id": cmd.id,
                    "timestamp": now.isoformat().replace("+00:00", "Z"),
                    "error_code": "TIMEOUT",
                    "error_message": f"Command timed out before completion (was {cmd.status})"
                }
                await manager.broadcast(payload)
            else:
                await session.rollback()

async def timeout_worker_loop():
    logger.info("Starting timeout worker loop")
    
    # Initialize redis listener for broadcast capability (handled in main or manager, but we'll do it here if it wasn't done)
    if manager._listener_task is None:
        manager._listener_task = asyncio.create_task(manager.listen_to_redis())
    
    try:
        while True:
            try:
                await scan_for_timeouts()
            except Exception as e:
                logger.error(f"Error in timeout scanner: {e}", exc_info=True)
                
            await asyncio.sleep(5.0)  # Scan every 5 seconds
    except asyncio.CancelledError:
        logger.info("Timeout scanner task cancelled. Exiting.")
        if manager._listener_task:
            manager._listener_task.cancel()


async def main():
    logger.info("Initializing standalone telemetry background processor...")
    # Add a small delay to allow database/redis to finish booting in docker
    await asyncio.sleep(2.0)
    
    # Run sync worker, retention pruner, and timeout scanner tasks concurrently
    try:
        await asyncio.gather(
            redis_to_db_sync_worker(),
            db_pruner_task(),
            timeout_worker_loop()
        )
    except KeyboardInterrupt:
        logger.info("Shutting down background processor.")


if __name__ == "__main__":
    asyncio.run(main())
