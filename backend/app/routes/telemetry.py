"""
API v1 routes — thin handlers that delegate to service layer.

All routes are mounted under ``/api/v1`` prefix.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from datetime import datetime, timezone
from app.schemas import TelemetryCreate, CommandCreate
from app.websocket_manager import manager
from app.services.analytics_service import AnalyticsService
from app.services.robot_service import RobotService
from app.services.telemetry_service import TelemetryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["v1"])


# ── Telemetry ───────────────────────────────────────────────────────


@router.post("/telemetry")
async def create_telemetry(data: TelemetryCreate, db: AsyncSession = Depends(get_db)):
    """Ingest a telemetry reading from the simulator."""
    service = TelemetryService(db)
    return await service.ingest(data)


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

@router.post("/commands/{robot_id}")
async def send_command(robot_id: int, command: CommandCreate):
    """Queue a command for a specific robot and broadcast it."""
    # Push to redis list so simulator can poll it
    await manager.redis.rpush(f"commands:{robot_id}", command.action)
    
    payload = {
        "type": "COMMAND",
        "robot_id": robot_id,
        "action": command.action,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }
    await manager.broadcast(payload)
    return {"message": "Command sent", "payload": payload}

@router.get("/commands/{robot_id}")
async def get_commands(robot_id: int):
    """Simulator polling endpoint to fetch pending commands."""
    cmds = []
    while True:
        cmd = await manager.redis.lpop(f"commands:{robot_id}")
        if not cmd:
            break
        cmds.append(cmd.decode("utf-8") if isinstance(cmd, bytes) else cmd)
    return cmds


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
