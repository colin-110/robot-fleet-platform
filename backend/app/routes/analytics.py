"""
API v1 routes — Fleet analytics.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.analytics_service import AnalyticsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["analytics"])


@router.get("/analytics/fleet")
async def fleet_analytics(db: AsyncSession = Depends(get_db)):
    """Return fleet-wide analytics (trends, distributions, missions)."""
    service = AnalyticsService(db)
    return await service.get_fleet_analytics()
