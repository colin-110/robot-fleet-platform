"""Event persistence."""

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event
from app.schemas import EventCreate


async def insert_events(db: AsyncSession, rows: list[tuple[EventCreate, datetime]]) -> list[Event]:
    """Insert events, skipping any already stored. Returns the new rows.

    The caller commits.
    """
    if not rows:
        return []
    stmt = (
        pg_insert(Event)
        .values([{"robot_id": e.robot_id, "message": e.message, "timestamp": ts} for e, ts in rows])
        .on_conflict_do_nothing(constraint="uq_events_robot_time_message")
        .returning(Event)
    )
    return list((await db.execute(stmt)).scalars().all())
