"""
API v1 routes — Robot command dispatch and status management.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.repositories.command_repo import CommandRepository
from app.schemas import CommandCreate, CommandStatusUpdate
from app.services.command_service import CommandService
from app.websocket_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["commands"])


@router.post("/commands/{robot_id}")
async def send_command(
    robot_id: int,
    command: CommandCreate,
    db: AsyncSession = Depends(get_db),
):
    """Queue a command for a specific robot and broadcast it."""
    service = CommandService(db)
    payload = await service.create_command(robot_id, command)
    return {"message": "Command sent", "payload": payload}


@router.get("/commands/{robot_id}")
async def get_commands(
    robot_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Simulator polling endpoint to fetch pending commands atomically."""
    repo = CommandRepository(db)
    cmds = []

    while True:
        record = await repo.get_next_pending(robot_id)
        if not record:
            break

        now = datetime.now(timezone.utc)
        dispatched = await repo.try_dispatch(record.id, now)

        if dispatched:
            cmd_dict = {
                "id": record.id,
                "command_type": record.command_type,
                "payload": record.payload,
            }
            cmds.append(cmd_dict)

            # Broadcast update
            broadcast_payload = {
                "type": "COMMAND_UPDATE",
                "robot_id": robot_id,
                "command_type": record.command_type,
                "status": "DISPATCHED",
                "command_id": record.id,
                "timestamp": now.isoformat().replace("+00:00", "Z"),
            }
            await manager.broadcast(broadcast_payload)
        else:
            # Another process grabbed it, try the next one
            continue

    return cmds


@router.patch("/commands/{command_id}/status")
async def update_command_status(
    command_id: str,
    update_data: CommandStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update the status of an existing command."""
    service = CommandService(db)
    payload = await service.update_status(command_id, update_data)
    return {"message": "Command status updated", "payload": payload}
