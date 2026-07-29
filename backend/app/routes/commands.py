"""
API v1 routes — Robot command dispatch and status management.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_role, verify_api_key, verify_console_access
from app.database import get_db
from app.repositories.command_repo import CommandRepository
from app.schemas import CommandCreate, CommandStatusUpdate
from app.security import OPERATOR
from app.services.command_service import CommandService
from app.websocket_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["commands"])


@router.post("/commands/{robot_id}")
async def send_command(
    robot_id: int,
    command: CommandCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_console_access),
    __=Depends(require_role(OPERATOR)),
):
    """Queue a command for a specific robot and broadcast it.

    Two independent checks, because they answer different questions.
    ``verify_console_access`` asks whether this *client* holds a usable
    credential (master key or console ticket). ``require_role`` asks whether
    the *person* is allowed to drive robots — in open mode everyone is, in
    required mode a viewer is not. Dropping either one would let a valid
    ticket act on behalf of a read-only account, or a signed-in operator act
    without a ticket.

    Console-scoped: the dashboard authenticates with a short-lived ticket, so
    dispatching a command no longer requires shipping the ingest key to the
    browser. ``verify_api_key`` still guards ingest.
    """
    service = CommandService(db)
    payload = await service.create_command(robot_id, command)
    return {"message": "Command sent", "payload": payload}


async def _claim_pending_commands(robot_id: int, db: AsyncSession) -> list[dict]:
    """Atomically claim every PENDING command for a robot.

    Each candidate is claimed with a conditional ``UPDATE ... WHERE
    status='PENDING'``, so when several pollers race for the same command
    exactly one wins and the losers move on to the next.
    """
    repo = CommandRepository(db)
    claimed: list[dict] = []

    while True:
        record = await repo.get_next_pending(robot_id)
        if not record:
            break

        now = datetime.now(timezone.utc)
        if not await repo.try_dispatch(record.id, now):
            # Another poller got it — try the next one.
            continue

        claimed.append(
            {
                "id": record.id,
                "command_type": record.command_type,
                "payload": record.payload,
            }
        )

        await manager.broadcast(
            {
                "type": "COMMAND_UPDATE",
                "robot_id": robot_id,
                "command_type": record.command_type,
                "status": "DISPATCHED",
                "command_id": record.id,
                "timestamp": now.isoformat().replace("+00:00", "Z"),
            }
        )

    return claimed


@router.post("/commands/{robot_id}/claim")
async def claim_commands(
    robot_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """Claim pending commands for a robot, transitioning them to DISPATCHED.

    POST rather than GET: this mutates state. A GET that dispatches commands
    breaks the safe-method contract — any crawler, prefetcher, or retrying
    proxy would silently consume the robot's command queue.
    """
    return await _claim_pending_commands(robot_id, db)


@router.get("/commands/{robot_id}", deprecated=True)
async def get_commands(
    robot_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """Deprecated alias for ``POST /commands/{robot_id}/claim``.

    Kept so already-deployed simulators keep working through a rollout; new
    clients should use the POST endpoint.
    """
    return await _claim_pending_commands(robot_id, db)


@router.patch("/commands/{command_id}/status")
async def update_command_status(
    command_id: str,
    update_data: CommandStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """Update the status of an existing command."""
    service = CommandService(db)
    payload = await service.update_status(command_id, update_data)
    return {"message": "Command status updated", "payload": payload}
