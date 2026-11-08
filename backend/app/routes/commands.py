"""
API v1 routes - Robot command dispatch and status management.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import FleetCaller, require_role, verify_console_access, verify_fleet_key
from app.database import get_db
from app.repositories.command_repo import CommandRepository
from app.schemas import CommandCreate, CommandStatusUpdate
from app.security import OPERATOR
from app.services.command_service import CommandService, broadcast_dispatched

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
    the *person* is allowed to drive robots - in open mode everyone is, in
    required mode a viewer is not. Dropping either one would let a valid
    ticket act on behalf of a read-only account, or a signed-in operator act
    without a ticket.
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
            # Another poller got it - try the next one.
            continue

        claimed.append(
            {
                "id": record.id,
                "command_type": record.command_type,
                "payload": record.payload,
            }
        )

        await broadcast_dispatched(robot_id, record.command_type, record.id, now)

    return claimed


@router.post("/commands/{robot_id}/claim")
async def claim_commands(
    robot_id: int,
    db: AsyncSession = Depends(get_db),
    caller: FleetCaller = Depends(verify_fleet_key),
):
    """Claim pending commands for a robot, transitioning them to DISPATCHED.

    POST rather than GET: this mutates state. A GET that dispatches commands
    breaks the safe-method contract - any crawler, prefetcher, or retrying
    proxy would silently consume the robot's command queue.
    """
    caller.ensure_robot(robot_id)
    return await _claim_pending_commands(robot_id, db)


@router.get("/commands/{robot_id}", deprecated=True)
async def get_commands(
    robot_id: int,
    db: AsyncSession = Depends(get_db),
    caller: FleetCaller = Depends(verify_fleet_key),
):
    """Deprecated alias for ``POST /commands/{robot_id}/claim``.

    Kept so already-deployed simulators keep working through a rollout; new
    clients should use the POST endpoint.
    """
    caller.ensure_robot(robot_id)
    return await _claim_pending_commands(robot_id, db)


@router.patch("/commands/{command_id}/status")
async def update_command_status(
    command_id: str,
    update_data: CommandStatusUpdate,
    db: AsyncSession = Depends(get_db),
    caller: FleetCaller = Depends(verify_fleet_key),
):
    """Update the status of an existing command."""
    service = CommandService(db)
    if caller.robot_id is not None:
        caller.ensure_robot(await service.get_robot_id(command_id))
    payload = await service.update_status(command_id, update_data)
    return {"message": "Command status updated", "payload": payload}
