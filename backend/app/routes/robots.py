"""
API v1 routes - Robot status and fleet overview.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import device_keys, presence
from app.auth import require_role, verify_console_access, verify_key_admin, verify_read_access
from app.database import get_db
from app.mqtt import config as mqtt_config
from app.mqtt import wire
from app.repositories import config_repo
from app.repositories.robot_repo import RobotRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.schemas import (
    DeviceKeyResponse,
    RobotConfigResponse,
    RobotConfigUpdate,
    RobotStatusResponse,
    TrackPoint,
)
from app.security import OPERATOR
from app.services import series_service, track_service
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


@router.delete("/robots/{robot_id}", status_code=204)
async def retire_robot(
    request: Request,
    robot_id: int = Path(..., ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_key_admin),
):
    """Retire a robot: off the fleet view, its key revoked, its open alarms
    cleared, and its retained broker messages removed so it is not announced
    again. Its history is kept. Issuing it a new key brings it back."""
    from app.alarms import engine as alarm_engine
    from app.intelligence import fleet
    from app.mqtt import topics

    if not await RobotRepository(db).retire(robot_id):
        raise HTTPException(status_code=404, detail="Robot not found")
    engine = alarm_engine.engine
    if engine is not None:
        await engine.retire(db, robot_id)
    await db.commit()
    if engine is not None:
        await engine.notify_pending()
    device_keys.set_hash(robot_id, None)
    presence.forget(robot_id)
    fleet.forget(robot_id)
    consumer = getattr(request.app.state, "mqtt", None)
    if consumer is not None:
        # An empty retained message deletes the retained one.
        for channel in (topics.PRESENCE, topics.CONFIG_REPORTED, topics.CONFIG, topics.STORED):
            consumer.client.publish(topics.topic(robot_id, channel), b"", qos=1, retain=True)
    logger.info("Retired robot %d", robot_id)


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


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@router.get("/robots/{robot_id}/track", response_model=list[TrackPoint])
async def robot_track(
    start: datetime,
    end: datetime,
    step_s: float = Query(1.0, gt=0, le=3600),
    robot_id: int = Path(..., ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_read_access),
):
    """Position and battery every ``step_s`` seconds, rebuilt from keyframes."""
    start, end = _utc(start), _utc(end)
    problem = track_service.check_window(start, end, step_s)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    rows = await TelemetryRepository(db).keyframes_between(robot_id, start, end)
    if not rows:
        # Raw keyframes older than RAW_RETENTION_DAYS are gone; their history
        # is in the line segments, within each field's error bound.
        series = {
            name: await series_service.series(db, robot_id, name, start, end)
            for name in ("x", "y", "battery")
        }
        return track_service.from_segments(series, start, end, step_s)
    config = await config_repo.get_config(db, robot_id)
    applied = config.reported if config else None
    return track_service.reconstruct(rows, start, end, step_s, applied)


@router.get("/robots/{robot_id}/series")
async def robot_series(
    field: str,
    start: datetime,
    end: datetime,
    robot_id: int = Path(..., ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_read_access),
):
    """One field over time, from line segments (``app/services/series_service.py``).

    Numeric fields come back as ``segments`` (``value(t) = v0 + slope * (t -
    t0)`` for ``t0 <= t <= t1``) with their ``error_bound``; status and
    mission fields as ``changes``. Time no segment covers is unknown. At most
    seven days per request.
    """
    start, end = _utc(start), _utc(end)
    problem = series_service.check(field, start, end)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    return await series_service.series(db, robot_id, field, start, end)


@router.get("/robots/{robot_id}/history")
async def robot_history(
    start: datetime,
    end: datetime,
    resolution: str = Query("minute", pattern="^(minute|hour)$"),
    robot_id: int = Path(..., ge=0),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_read_access),
):
    """Exact time-weighted rollups (``app/storage/rollups.py``), oldest first.

    Minutes are kept a year and hours indefinitely; raw keyframes, 30 days.
    ``seconds_covered`` says how much of each bucket the robot was heard
    from; the averages are over that time only.
    """
    from sqlalchemy import select

    from app.models import TelemetryHour, TelemetryMinute

    model = TelemetryMinute if resolution == "minute" else TelemetryHour
    start, end = _utc(start), _utc(end)
    limit = 2000
    rows = (
        await db.execute(
            select(model)
            .where(model.robot_id == robot_id, model.bucket >= start, model.bucket < end)
            .order_by(model.bucket)
            .limit(limit)
        )
    ).scalars()
    columns = [c.name for c in model.__table__.columns if c.name != "robot_id"]
    return [{c: getattr(r, c) for c in columns} for r in rows]
