"""Tell robots which of their readings are safely stored.

Every ingest response carries an ``ack``::

    {"server": "<boot id>", "durable_until": {"7": "2026-11-07T12:00:05Z"}}

``durable_until`` is, per robot, the newest timestamp such that every reading
this server accepted from that robot up to that time is stored in Postgres (or
in the Redis stream, when OPT_REDIS_BUFFER is on). A robot keeps each reading
it has sent until it is covered by ``durable_until``.

``server`` changes whenever the process restarts. Readings the old process
had accepted but not yet stored (the deferred-write buffer) are gone at that
point, so a robot that sees a new id resends everything not yet covered.
Writes skip readings already stored, so resending is always safe.

Assumes one process per deployment when DEFER_WRITES_WITHOUT_VIEWERS is on,
since the buffer and these watermarks are per process.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select

from app import telemetry_buffer
from app.database import AsyncSessionLocal
from app.models import Robot

BOOT_ID = secrets.token_hex(8)

_ONE_MICROSECOND = timedelta(microseconds=1)
_stored: dict[int, datetime] = {}


def mark_stored(readings: Iterable[tuple[int, datetime]]) -> None:
    """Record readings that are now stored (or queued on the stream)."""
    for robot_id, ts in readings:
        if robot_id not in _stored or ts > _stored[robot_id]:
            _stored[robot_id] = ts


def durable_until(robot_id: int) -> datetime | None:
    stored = _stored.get(robot_id)
    if stored is None:
        return None
    pending = telemetry_buffer.oldest_pending(robot_id)
    if pending is not None and pending <= stored:
        # A reading at or before ``stored`` is still only in memory, so the
        # watermark has to stop just short of it.
        return pending - _ONE_MICROSECOND
    return stored


def ack(robot_ids: Iterable[int]) -> dict:
    def iso(value: datetime | None) -> str | None:
        return value.isoformat().replace("+00:00", "Z") if value else None

    return {
        "server": BOOT_ID,
        "durable_until": {str(rid): iso(durable_until(rid)) for rid in sorted(set(robot_ids))},
    }


def reset() -> None:
    """Forget all watermarks (used by tests)."""
    _stored.clear()


async def load() -> int:
    """Seed watermarks from the roster, whose last_seen is only ever
    advanced in the same transaction that stores the reading."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Robot.id, Robot.last_seen).where(Robot.last_seen.isnot(None))
        )
        rows = result.all()
    mark_stored((row.id, row.last_seen) for row in rows)
    return len(rows)
