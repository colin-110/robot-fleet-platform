"""Robot settings: desired by the server, reported by the robot."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RobotConfig


async def get_config(db: AsyncSession, robot_id: int) -> RobotConfig | None:
    return await db.get(RobotConfig, robot_id)


async def set_desired(db: AsyncSession, robot_id: int, values: dict) -> RobotConfig:
    """Replace the desired settings and bump the version. The caller commits."""
    now = datetime.now(timezone.utc)
    stmt = pg_insert(RobotConfig).values(
        robot_id=robot_id, desired=values, version=1, updated_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[RobotConfig.robot_id],
        set_={
            "desired": stmt.excluded.desired,
            "version": RobotConfig.version + 1,
            "updated_at": stmt.excluded.updated_at,
        },
    ).returning(RobotConfig)
    return (await db.execute(stmt)).scalar_one()


async def set_reported(
    db: AsyncSession, robot_id: int, version: int, applied: dict, rejected: dict
) -> None:
    """Record what the robot applied. The caller commits."""
    now = datetime.now(timezone.utc)
    stmt = pg_insert(RobotConfig).values(
        robot_id=robot_id,
        desired={},
        version=0,
        reported=applied,
        reported_rejected=rejected,
        reported_version=version,
        reported_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[RobotConfig.robot_id],
        set_={
            "reported": stmt.excluded.reported,
            "reported_rejected": stmt.excluded.reported_rejected,
            "reported_version": stmt.excluded.reported_version,
            "reported_at": stmt.excluded.reported_at,
        },
    )
    await db.execute(stmt)


async def list_desired(db: AsyncSession) -> list[tuple[int, int, dict]]:
    rows = await db.execute(
        select(RobotConfig.robot_id, RobotConfig.version, RobotConfig.desired).where(
            RobotConfig.version > 0
        )
    )
    return [(robot_id, version, desired) for robot_id, version, desired in rows]
