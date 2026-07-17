import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta

from sqlalchemy import insert, delete
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Telemetry
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
    
    while True:
        try:
            payloads = []
            # Batch read up to 100 items
            for _ in range(100):
                data = await redis.rpop("telemetry_queue")
                if not data:
                    break
                payloads.append(json.loads(data.decode("utf-8") if isinstance(data, bytes) else data))
                
            if payloads:
                insert_dicts = []
                for p in payloads:
                    ts = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
                    mst = datetime.fromisoformat(p["mission_start_time"].replace("Z", "+00:00")) if p["mission_start_time"] else None
                    
                    insert_dicts.append({
                        "robot_id": p["robot_id"],
                        "battery": p["battery"],
                        "temperature": p["temperature"],
                        "speed": p["speed"],
                        "status": p["status"],
                        "mission_id": p["mission_id"],
                        "mission_type": p["mission_type"],
                        "mission_progress": p["mission_progress"],
                        "mission_start_time": mst,
                        "battery_health": p["battery_health"],
                        "motor_health": p["motor_health"],
                        "sensor_health": p["sensor_health"],
                        "network_health": p["network_health"],
                        "x": p["x"],
                        "y": p["y"],
                        "timestamp": ts
                    })
                
                # Perform bulk insert
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        stmt = insert(Telemetry).values(insert_dicts)
                        await session.execute(stmt)
                        
                logger.info("Successfully batched and saved %d telemetry records to database.", len(insert_dicts))
                # Reset retry delay on successful database write
                retry_delay = 2.0
            
            if not payloads:
                # Idle sleep
                await asyncio.sleep(0.5)
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


async def main():
    logger.info("Initializing standalone telemetry background processor...")
    # Add a small delay to allow database/redis to finish booting in docker
    await asyncio.sleep(2.0)
    
    # Run sync worker and retention pruner tasks concurrently
    try:
        await asyncio.gather(
            redis_to_db_sync_worker(),
            db_pruner_task()
        )
    except KeyboardInterrupt:
        logger.info("Shutting down background processor.")


if __name__ == "__main__":
    asyncio.run(main())
