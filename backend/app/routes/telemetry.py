"""
API v1 routes — thin handlers that delegate to service layer.

All routes are mounted under ``/api/v1`` prefix.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from datetime import datetime, timezone
from app.schemas import TelemetryCreate, CommandCreate, CommandStatusUpdate
from app.websocket_manager import manager
from app.services.analytics_service import AnalyticsService
from app.services.robot_service import RobotService
from app.services.telemetry_service import TelemetryService

settings = get_settings()

async def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != settings.telemetry_api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["v1"])


# ── Telemetry ───────────────────────────────────────────────────────


@router.post("/telemetry")
async def create_telemetry(
    data: TelemetryCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _ = Depends(verify_api_key)
):
    """Ingest a telemetry reading from the simulator."""
    service = TelemetryService(db)
    return await service.ingest(data, background_tasks)


@router.get("/telemetry")
async def get_telemetry(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Return the most recent telemetry rows."""
    service = TelemetryService(db)
    return await service.get_recent(limit=min(limit, 200))


# ── Events & Commands ───────────────────────────────────────────────

from pydantic import BaseModel

class EventCreate(BaseModel):
    robot_id: int
    message: str

@router.post("/events")
async def create_event(event: EventCreate):
    """Ingest a generic event from a robot and broadcast it."""
    payload = {
        "type": "EVENT",
        "robot_id": event.robot_id,
        "message": event.message,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }
    await manager.broadcast(payload)
    return {"message": "Event logged"}

from app.services.command_service import CommandService
import json
from sqlalchemy import select
from app.models import RobotCommand

@router.post("/commands/{robot_id}")
async def send_command(robot_id: int, command: CommandCreate, db: AsyncSession = Depends(get_db)):
    """Queue a command for a specific robot and broadcast it."""
    service = CommandService(db)
    payload = await service.create_command(robot_id, command)
    
    # We still push to Redis for the simulator to poll, but we don't strictly have to if simulator polls DB.
    # The existing architecture has simulator polling /commands/{robot_id} which reads from Redis.
    # To keep the atomic dispatch requirement, the /commands endpoint should read directly from DB using FOR UPDATE SKIP LOCKED.
    # We no longer need to push to redis.
    
    return {"message": "Command sent", "payload": payload}

@router.get("/commands/{robot_id}")
async def get_commands(robot_id: int, db: AsyncSession = Depends(get_db)):
    """Simulator polling endpoint to fetch pending commands atomically."""
    # Atomic dispatch: SELECT ... FOR UPDATE SKIP LOCKED
    # SQLite does not support FOR UPDATE SKIP LOCKED, so we have to fallback to simple UPDATE for local dev.
    # Since Postgres supports it, we'll try to write it using SQLAlchemy's with_for_update(skip_locked=True)
    # However, sqlite will throw an error with skip_locked.
    # A portable way for both is to just select a pending ID, then UPDATE WHERE id = X AND status = 'PENDING'.
    # If the UPDATE returns 1 row affected, we acquired it.
    
    cmds = []
    
    while True:
        # Find one pending command
        stmt = select(RobotCommand).where(
            RobotCommand.robot_id == robot_id,
            RobotCommand.status == "PENDING"
        ).order_by(RobotCommand.created_at.asc()).limit(1)
        
        result = await db.execute(stmt)
        record = result.scalars().first()
        
        if not record:
            break
            
        # Try to atomically update it to DISPATCHED
        from sqlalchemy import update
        now = datetime.now(timezone.utc)
        update_stmt = (
            update(RobotCommand)
            .where(
                RobotCommand.id == record.id,
                RobotCommand.status == "PENDING"
            )
            .values(status="DISPATCHED", dispatched_at=now)
            .execution_options(synchronize_session=False)
        )
        
        update_result = await db.execute(update_stmt)
        if update_result.rowcount > 0:
            await db.commit()
            
            # Use CommandService._to_dict logic (simplified here)
            cmd_dict = {
                "id": record.id,
                "command_type": record.command_type,
                "payload": record.payload
            }
            cmds.append(cmd_dict)
            
            # Broadcast update
            broadcast_payload = {
                "type": "COMMAND_UPDATE",
                "robot_id": robot_id,
                "command_type": record.command_type,
                "status": "DISPATCHED",
                "command_id": record.id,
                "timestamp": now.isoformat().replace("+00:00", "Z")
            }
            await manager.broadcast(broadcast_payload)
        else:
            # Another process grabbed it, continue loop to try finding another
            await db.rollback()
            continue

    return cmds

@router.patch("/commands/{command_id}/status")
async def update_command_status(command_id: str, update_data: CommandStatusUpdate, db: AsyncSession = Depends(get_db)):
    service = CommandService(db)
    payload = await service.update_status(command_id, update_data)
    return {"message": "Command status updated", "payload": payload}


# ── Robot Status ────────────────────────────────────────────────────


@router.get("/robots/status")
async def robot_status(db: AsyncSession = Depends(get_db)):
    """Return current status summary for all robots."""
    service = RobotService(db)
    return await service.get_fleet_status()


# ── Fleet Analytics ─────────────────────────────────────────────────


@router.get("/analytics/fleet")
async def fleet_analytics(db: AsyncSession = Depends(get_db)):
    """Return fleet-wide analytics (trends, distributions, missions)."""
    service = AnalyticsService(db)
    return await service.get_fleet_analytics()
