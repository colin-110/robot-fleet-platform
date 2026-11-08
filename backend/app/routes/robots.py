"""
API v1 routes - Robot status and fleet overview.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import device_keys
from app.auth import require_role, verify_console_access, verify_key_admin, verify_read_access
from app.database import get_db
from app.mqtt import config as mqtt_config
from app.mqtt import wire
from app.repositories import config_repo
from app.repositories.robot_repo import RobotRepository
from app.schemas import (
    DeviceKeyResponse,
    RobotConfigResponse,
    RobotConfigUpdate,
    RobotStatusResponse,
)
from app.security import OPERATOR
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


def _config_response(robot_id: int, row) -> RobotConfigResponse:
    if row is None:
        return RobotConfigResponse(robot_id=robot_id, desired={}, version=0, in_sync=True)
    return RobotConfigResponse(
        robot_id=robot_id,
        desired=row.desired,
        version=row.version,
        updated_at=row.updated_at,
        reported=row.reported,
        reported_rejected=row.reported_rejected,
        reported_version=row.reported_version,
        reported_at=row.reported_at,
        in_sync=row.version == 0 or row.reported_version == row.version,
    )


@router.get("/robots/{robot_id}/config", response_model=RobotConfigResponse)
async def get_robot_config(
    robot_id: int = Path(..., ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_read_access),
):
    """The robot's desired settings and what it last reported applying."""
    return _config_response(robot_id, await config_repo.get_config(db, robot_id))


@router.put("/robots/{robot_id}/config", response_model=RobotConfigResponse)
async def set_robot_config(
    body: RobotConfigUpdate,
    robot_id: int = Path(..., ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_console_access),
    __=Depends(require_role(OPERATOR)),
):
    """Replace the robot's desired settings and push them to it.

    Unknown settings and out-of-range values are refused here, with the same
    rules the agent applies (``wire.CONFIG_FIELDS``).
    """
    accepted, rejected = wire.check_config(body.values)
    if rejected:
        raise HTTPException(status_code=422, detail={"rejected": rejected})
    row = await config_repo.set_desired(db, robot_id, accepted)
    await db.commit()
    if mqtt_config.publisher is not None:
        mqtt_config.publisher.publish(robot_id, row.version, row.desired)
    logger.info("Robot %d settings are now version %d: %s", robot_id, row.version, accepted)
    return _config_response(robot_id, row)
