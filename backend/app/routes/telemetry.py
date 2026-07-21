"""
API v1 routes — Telemetry ingestion and retrieval.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.schemas import TelemetryCreate
from app.services.telemetry_service import TelemetryService

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["telemetry"])


async def verify_api_key(x_api_key: str = Header(None)):
    """Validate the API key header for telemetry ingestion."""
    if x_api_key != settings.telemetry_api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")


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


@router.get("/telemetry")
async def get_telemetry(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Return the most recent telemetry rows."""
    service = TelemetryService(db)
    return await service.get_recent(limit=min(limit, 200))
