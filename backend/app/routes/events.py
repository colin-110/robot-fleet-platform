"""
API v1 routes - Robot events.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth import FleetCaller, verify_fleet_key, verify_read_access
from app.database import get_db
from app.models import Event
from app.repositories.event_repo import insert_events
from app.schemas import EventCreate, EventResponse
from app.services.event_service import broadcast_events
from app.services.telemetry_service import resolve_timestamp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events")
async def create_event(
    event: EventCreate,
    db: AsyncSession = Depends(get_db),
    caller: FleetCaller = Depends(verify_fleet_key),
):
    """Store an event from a robot and broadcast it. Resending it is harmless."""
    caller.ensure_robot(event.robot_id)
    ts = resolve_timestamp(event.timestamp, datetime.now(timezone.utc))
    inserted = await insert_events(db, [(event, ts)])
    await db.commit()
    if not inserted:
        return {"message": "Event already received"}
    await broadcast_events(inserted)
    return {"message": "Event logged", "id": inserted[0].id}


@router.get("/events", response_model=list[EventResponse])
async def get_events(
    limit: int = Query(50, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_read_access),
):
    """Get recent events."""
    stmt = select(Event).order_by(Event.timestamp.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
