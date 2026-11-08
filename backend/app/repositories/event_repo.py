"""Event persistence."""

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event
from app.schemas import EventCreate

# Postgres takes at most 32,767 parameters per statement (3 per event).
MAX_ROWS_PER_INSERT = 5000


async def insert_events(db: AsyncSession, rows: list[tuple[EventCreate, datetime]]) -> list[Event]:
    """Insert events, skipping any already stored. Returns the new rows.

    The caller commits.
    """
    values = [{"robot_id": e.robot_id, "message": e.message, "timestamp": ts} for e, ts in rows]
    inserted: list[Event] = []
    for start in range(0, len(values), MAX_ROWS_PER_INSERT):
        stmt = (
            pg_insert(Event)
            .values(values[start : start + MAX_ROWS_PER_INSERT])
            .on_conflict_do_nothing(constraint="uq_events_robot_time_message")
            .returning(Event)
        )
        inserted.extend((await db.execute(stmt)).scalars().all())
    return inserted
