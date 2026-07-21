"""
API v1 routes — Robot events.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.schemas import EventCreate
from app.websocket_manager import manager
from app.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["events"])


@router.post("/events")
async def create_event(
    event: EventCreate,
    _=Depends(verify_api_key),
):
    """Ingest a generic event from a robot and broadcast it."""
    payload = {
        "type": "EVENT",
        "robot_id": event.robot_id,
        "message": event.message,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    await manager.broadcast(payload)
    return {"message": "Event logged"}
