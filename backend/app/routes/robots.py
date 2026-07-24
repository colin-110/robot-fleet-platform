"""
API v1 routes — Robot status and fleet overview.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.robot_service import RobotService
from app.schemas import RobotStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["robots"])


from fastapi import APIRouter, Depends, Query

@router.get("/robots/status", response_model=list[RobotStatusResponse])
async def robot_status(
    limit: int = Query(1000, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Return current status summary for all robots."""
    service = RobotService(db)
    return await service.get_fleet_status(limit=limit, skip=skip)
