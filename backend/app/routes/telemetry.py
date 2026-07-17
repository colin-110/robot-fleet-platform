"""
API v1 routes — thin handlers that delegate to service layer.

All routes are mounted under ``/api/v1`` prefix.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import TelemetryCreate
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
