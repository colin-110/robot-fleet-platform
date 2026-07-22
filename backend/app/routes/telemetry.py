"""
API v1 routes — Telemetry ingestion and retrieval.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.config import get_settings
from app.database import get_db
from app.schemas import TelemetryCreate, TelemetryResponse
from app.services.telemetry_service import TelemetryService
from app.auth import verify_api_key

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["telemetry"])


@router.post("/telemetry")
async def create_telemetry(
    data: TelemetryCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """Ingest a telemetry reading from the simulator."""
    service = TelemetryService(db)
    return await service.ingest(data, background_tasks)

@router.post("/telemetry/batch")
async def create_telemetry_batch(
    data: List[TelemetryCreate],
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """Ingest a batch of telemetry readings."""
    service = TelemetryService(db)
    return await service.ingest_batch(data, background_tasks)

@router.get("/telemetry", response_model=List[TelemetryResponse])
async def get_telemetry(
    limit: int = 50,
    skip: int = 0,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """Return the most recent telemetry rows."""
    service = TelemetryService(db)
    return await service.get_recent(limit=min(limit, 200), skip=skip)
