"""
API v1 routes — Robot events.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models import Event
from app.schemas import EventCreate, EventResponse
from app.websocket_manager import manager
from app.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events")
async def create_event(
    event: EventCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """Ingest a generic event from a robot, persist it, and broadcast it."""
    new_event = Event(
        robot_id=event.robot_id,
        message=event.message,
        timestamp=datetime.now(timezone.utc)
    )
    db.add(new_event)
    await db.commit()
    await db.refresh(new_event)

    payload = {
        "type": "EVENT",
        "robot_id": new_event.robot_id,
        "message": new_event.message,
        "timestamp": new_event.timestamp.isoformat().replace("+00:00", "Z") if new_event.timestamp else None,
    }
    await manager.broadcast(payload)
    return {"message": "Event logged", "id": new_event.id}

@router.get("/events", response_model=list[EventResponse])
async def get_events(
    limit: int = Query(50, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Get recent events."""
    stmt = select(Event).order_by(Event.timestamp.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
