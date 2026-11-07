"""
API v1 routes - Robot status and fleet overview.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import device_keys
from app.auth import verify_key_admin, verify_read_access
from app.database import get_db
from app.repositories.robot_repo import RobotRepository
from app.schemas import DeviceKeyResponse, RobotStatusResponse
from app.services.robot_service import RobotService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["robots"])


@router.get("/robots/status", response_model=list[RobotStatusResponse])
async def robot_status(
    limit: int = Query(1000, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_read_access),
):
    """Return current status summary for all robots."""
    service = RobotService(db)
    return await service.get_fleet_status(limit=limit, skip=skip)


@router.post("/robots/{robot_id}/key", response_model=DeviceKeyResponse)
async def issue_device_key(
    robot_id: int = Path(..., ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_key_admin),
):
    """Issue a new device key for a robot, invalidating its previous one.

    The key is only returned here. Only its hash is stored, so it cannot be
    retrieved later. Registers the robot if it is not in the roster yet.
    """
    key = device_keys.generate(robot_id)
    key_hash = device_keys.hash_key(key)
    await RobotRepository(db).set_key_hash(robot_id, key_hash)
    await db.commit()
    device_keys.set_hash(robot_id, key_hash)
    logger.info("Issued device key for robot %d", robot_id)
    return DeviceKeyResponse(robot_id=robot_id, key=key)


@router.delete("/robots/{robot_id}/key", status_code=204)
async def revoke_device_key(
    robot_id: int = Path(..., ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_key_admin),
):
    """Revoke the robot's device key."""
    if not await RobotRepository(db).revoke_key(robot_id):
        raise HTTPException(status_code=404, detail="Robot not found")
    await db.commit()
    device_keys.set_hash(robot_id, None)
    logger.info("Revoked device keys for robot %d", robot_id)
