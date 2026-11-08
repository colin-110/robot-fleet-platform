"""Stored watermarks: how far each robot's outbox stream is committed."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StoredWatermark


async def load_watermarks(db: AsyncSession, robot_ids) -> dict[int, tuple[str, int]]:
    rows = await db.execute(
        select(StoredWatermark.robot_id, StoredWatermark.stream, StoredWatermark.seq).where(
            StoredWatermark.robot_id.in_(list(robot_ids))
        )
    )
    return {robot_id: (stream, seq) for robot_id, stream, seq in rows}


async def save_watermarks(db: AsyncSession, marks: dict[int, tuple[str, int]]) -> None:
    """Upsert watermarks. The caller commits."""
    if not marks:
        return
    now = datetime.now(timezone.utc)
    stmt = pg_insert(StoredWatermark).values(
        [
            {"robot_id": robot_id, "stream": stream, "seq": seq, "updated_at": now}
            for robot_id, (stream, seq) in marks.items()
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[StoredWatermark.robot_id],
        set_={
            "stream": stmt.excluded.stream,
            "seq": stmt.excluded.seq,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await db.execute(stmt)
