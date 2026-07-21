"""
API v1 routes — Robot status and fleet overview.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.robot_service import RobotService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["robots"])


@router.get("/robots/status")
async def robot_status(db: AsyncSession = Depends(get_db)):
    """Return current status summary for all robots."""
    service = RobotService(db)
    return await service.get_fleet_status()
