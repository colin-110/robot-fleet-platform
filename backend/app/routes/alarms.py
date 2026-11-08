"""Alarms: list them and acknowledge them."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alarms import engine as alarm_engine
from app.alarms.engine import CLEARED, AlarmEngine, alarm_dict
from app.auth import Principal, require_role, verify_console_access, verify_read_access
from app.database import get_db
from app.models import Alarm
from app.security import OPERATOR

router = APIRouter(prefix="/api/v1", tags=["alarms"])


@router.get("/alarms")
async def list_alarms(
    state: str = Query("open", pattern="^(open|all)$"),
    robot_id: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_read_access),
):
    """Open alarms (ACTIVE or ACKNOWLEDGED), or all of them, newest first."""
    stmt = select(Alarm).order_by(Alarm.raised_at.desc()).limit(limit)
    if state == "open":
        stmt = stmt.where(Alarm.state != CLEARED)
    if robot_id is not None:
        stmt = stmt.where(Alarm.robot_id == robot_id)
    return [alarm_dict(a) for a in (await db.execute(stmt)).scalars().all()]


@router.post("/alarms/{alarm_id}/ack")
async def acknowledge_alarm(
    alarm_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_console_access),
    principal: Principal = Depends(require_role(OPERATOR)),
):
    """Acknowledge an ACTIVE alarm. It stays open until its condition clears."""
    engine = alarm_engine.engine or AlarmEngine()
    alarm = await engine.acknowledge(db, alarm_id, principal.username)
    if alarm is None:
        exists = await db.get(Alarm, alarm_id)
        raise HTTPException(
            status_code=409 if exists else 404,
            detail="Alarm is not active" if exists else "Alarm not found",
        )
    return alarm
