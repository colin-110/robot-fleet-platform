"""
API v1 routes — Robot events.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth import verify_api_key, verify_read_access
from app.database import get_db
from app.models import Event
from app.schemas import EventCreate, EventResponse
from app.websocket_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events")
async def create_event(
    event: EventCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """Ingest a generic event from a robot, persist it, and broadcast it.

    With an ``idempotency_key``, a retried POST returns the event already
    logged under that key instead of logging and broadcasting it twice.
    """
    timestamp = datetime.now(timezone.utc)
    stmt = (
        pg_insert(Event)
        .values(
            robot_id=event.robot_id,
            message=event.message,
            idempotency_key=event.idempotency_key,
            timestamp=timestamp,
        )
        .on_conflict_do_nothing(index_elements=[Event.robot_id, Event.idempotency_key])
        .returning(Event.id)
    )
    event_id = (await db.execute(stmt)).scalar_one_or_none()
    await db.commit()

    if event_id is None:
        # Only a keyed event can conflict, and it is already logged and was
        # broadcast when it first arrived.
        existing = await db.execute(
            select(Event.id).where(
                Event.robot_id == event.robot_id,
                Event.idempotency_key == event.idempotency_key,
            )
        )
        return {"message": "Event logged", "id": existing.scalar_one()}

    payload = {
        "type": "EVENT",
        "robot_id": event.robot_id,
        "message": event.message,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
    }
    # event_stream, not the default telemetry_stream: that stream is a bounded
    # ingest buffer whose overflow drops unacknowledged telemetry, so events
    # sharing it would consume the worker's catch-up headroom.
    await manager.broadcast(payload, stream=manager.event_stream)
    return {"message": "Event logged", "id": event_id}


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
