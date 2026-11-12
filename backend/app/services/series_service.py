"""One field of one robot over time, from line segments.

Finished hours come from ``telemetry_lines`` (or ``telemetry_states`` for the
non-numeric fields), written by ``lines_job``. The hour still in progress
has no stored segments yet, so it is fitted from the raw keyframes on the fly
with the same code, and the caller cannot tell the difference except by the
``stored`` flag. Every response carries the error bound: the robot's own
tolerance (its keyframes against what it measured) plus the server's (the
segments against the keyframes).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import FleetSetting, Telemetry, TelemetryLine, TelemetryState
from app.repositories import config_repo
from app.storage import lines, lines_job

MAX_WINDOW = timedelta(days=7)
MAX_ITEMS = 20_000


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


def check(field: str, start: datetime, end: datetime) -> str | None:
    """Why the request cannot be served, or None."""
    if field not in lines.FIELDS and field not in lines.STATE_FIELDS:
        names = ", ".join([*lines.FIELDS, *lines.STATE_FIELDS])
        return f"Unknown field {field!r}; one of: {names}"
    if end <= start:
        return "end must be after start"
    if end - start > MAX_WINDOW:
        return f"At most {MAX_WINDOW.days} days per request"
    return None


async def _done_until(db: AsyncSession) -> float:
    saved = await db.get(FleetSetting, lines_job.WATERMARK_KEY)
    return saved.value["until"] if saved else float("-inf")


async def _live_keyframes(db, robot_id: int, start: float, end: float, heartbeat: float):
    rows = await db.execute(
        select(*(getattr(Telemetry, c) for c in lines_job.COLUMNS))
        .where(
            Telemetry.robot_id == robot_id,
            Telemetry.timestamp >= _dt(start - heartbeat * lines.HEARTBEAT_GRACE),
            Telemetry.timestamp < _dt(end),
        )
        .order_by(Telemetry.timestamp)
    )
    return [lines_job._keyframe(row) for row in rows]


async def _stored(db, robot_id: int, field: str, a: float, stop: float) -> list[dict]:
    """Stored segments or changes touching ``[a, stop)``, oldest first."""
    if field in lines.FIELDS:
        rows = await db.execute(
            select(TelemetryLine)
            .where(
                TelemetryLine.robot_id == robot_id,
                TelemetryLine.field == lines.FIELD_IDS[field],
                TelemetryLine.t0 < _dt(stop),
                TelemetryLine.t1 >= _dt(a),
            )
            .order_by(TelemetryLine.t0)
            .limit(MAX_ITEMS)
        )
        return [
            {"t0": r.t0, "t1": r.t1, "v0": r.v0, "slope": r.slope, "stored": True}
            for r in rows.scalars()
        ]
    # Every hour's first row is the value in force at its start, so reading
    # from the start of ``a``'s hour finds the value in force at ``a``.
    rows = await db.execute(
        select(TelemetryState)
        .where(
            TelemetryState.robot_id == robot_id,
            TelemetryState.field == lines.STATE_IDS[field],
            TelemetryState.t >= _dt(a // lines_job.BLOCK * lines_job.BLOCK),
            TelemetryState.t < _dt(stop),
        )
        .order_by(TelemetryState.t)
        .limit(MAX_ITEMS)
    )
    return [{"t": r.t, "value": r.value, "stored": True} for r in rows.scalars()]


def _hour_of(item: dict) -> float:
    t = item["t0"] if "t0" in item else item["t"]
    return t.timestamp() // lines_job.BLOCK * lines_job.BLOCK


async def series(
    db: AsyncSession, robot_id: int, field: str, start: datetime, end: datetime
) -> dict:
    scale = get_settings().line_tolerance_scale
    config = await config_repo.get_config(db, robot_id)
    field_tol, heartbeat = lines_job.tolerances(config.reported if config else None, 1.0)
    a, b = start.timestamp(), min(end.timestamp(), time.time())
    done = await _done_until(db)
    numeric = field in lines.FIELDS

    def fitted(frames, lo: float, hi: float) -> list[dict]:
        if numeric:
            found = lines.lines_for(
                frames, field, hi, heartbeat, field_tol[field] * scale, start=lo
            )
            return [
                {
                    "t0": _dt(ln.t0),
                    "t1": _dt(ln.t1),
                    "v0": ln.v0,
                    "slope": ln.slope,
                    "stored": False,
                }
                for ln in found
            ]
        return [
            {"t": _dt(t), "value": value, "stored": False}
            for t, value in lines.changes(frames, field, hi, heartbeat, lo)
        ]

    # Hour by hour: stored rows where the job wrote them, otherwise fitted
    # from the raw keyframes if those still exist (the hour still in
    # progress, or an hour done before this field was stored).
    stored = await _stored(db, robot_id, field, a, min(b, done)) if a < done else []
    by_hour: dict[float, list[dict]] = {}
    for item in stored:
        by_hour.setdefault(_hour_of(item), []).append(item)
    items: list[dict] = []
    hour = a // lines_job.BLOCK * lines_job.BLOCK
    gap_start = None
    while hour < b:
        lo = max(a, hour)
        if hour + lines_job.BLOCK <= done and hour in by_hour:
            if gap_start is not None:
                frames = await _live_keyframes(db, robot_id, gap_start, lo, heartbeat)
                items += fitted(frames, gap_start, lo)
                gap_start = None
            items += by_hour[hour]
        elif gap_start is None:
            gap_start = lo
        hour += lines_job.BLOCK
    if gap_start is not None:
        frames = await _live_keyframes(db, robot_id, gap_start, b, heartbeat)
        items += fitted(frames, gap_start, b)

    if not numeric:
        # Keep the change in force at ``a`` and everything after it.
        before = [i for i in items if i["t"].timestamp() <= a]
        items = before[-1:] + [i for i in items if i["t"].timestamp() > a]

    response = {"robot_id": robot_id, "field": field, "start": start, "end": end}
    if numeric:
        robot, server = field_tol[field], field_tol[field] * scale
        response["error_bound"] = {"robot": robot, "server": server, "total": robot + server}
        response["segments"] = items[:MAX_ITEMS]
    else:
        response["changes"] = items[:MAX_ITEMS]
    return response
