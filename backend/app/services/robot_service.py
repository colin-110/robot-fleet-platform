"""
Robot service — fleet status derivation.

Fleet status is the roster LEFT JOINed onto recent telemetry, not a GROUP BY
over recent telemetry. The distinction matters: deriving the fleet from
telemetry alone means a robot that stops reporting eventually falls outside the
query window and silently disappears from the dashboard. A monitoring system
that quietly forgets a unit it can no longer see has failed at its one job, so
every registered robot is always listed and missing telemetry renders OFFLINE.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache
from app.config import get_settings
from app.models import Robot
from app.repositories.robot_repo import RobotRepository
from app.repositories.telemetry_repo import TelemetryRepository

logger = logging.getLogger(__name__)
settings = get_settings()

OFFLINE = "OFFLINE"


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a timestamp to UTC.

    Rows read back from Postgres carry tzinfo, but some drivers hand back naive
    datetimes. Treat naive values as already-UTC rather than letting them
    silently compare against aware ones.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _derive_status(latest, age_seconds: float) -> str:
    """Report the robot's own status, unless it has gone quiet.

    One threshold, from configuration. This previously checked three different
    values in two places — ``>300`` and ``>60`` here and ``>180`` in the
    caller — of which ``>300`` was unreachable and ``>180`` was dead for any
    robot that reported a status.
    """
    if age_seconds > settings.offline_after_seconds:
        return OFFLINE
    return latest.status.upper() if latest.status else "ACTIVE"


def _estimate_runtime_minutes(rows, latest_ts: datetime) -> float | None:
    """Extrapolate remaining runtime from the observed battery drain rate.

    Returns ``None`` when the history spans no time or the battery is not
    falling — a flat or charging battery has no meaningful time-to-empty, and
    dividing by a zero span would raise.
    """
    if len(rows) < 2:
        return None

    oldest = rows[0]
    latest = rows[-1]
    span_minutes = (latest_ts - _as_utc(oldest.timestamp)).total_seconds() / 60.0
    drained = oldest.battery - latest.battery

    if span_minutes <= 0 or drained <= 0:
        return None

    drain_per_minute = drained / span_minutes
    effective_battery = latest.battery * ((latest.battery_health or 100.0) / 100.0)
    return round(effective_battery / drain_per_minute, 1)


def summarize_robot_history(rows) -> dict | None:
    """Condense a robot's recent telemetry into a single status payload."""
    if not rows:
        return None

    # Rows arrive oldest-first from the window-function query.
    latest = rows[-1]
    now = datetime.now(timezone.utc)
    ts = _as_utc(latest.timestamp)
    age_seconds = (now - ts).total_seconds()

    return {
        "robot_id": latest.robot_id,
        "battery": round(latest.battery, 2),
        "temperature": round(latest.temperature, 2),
        "speed": round(latest.speed, 2),
        "status": _derive_status(latest, age_seconds),
        "mission_id": latest.mission_id,
        "mission_type": latest.mission_type,
        "mission_progress": round(latest.mission_progress, 1)
        if latest.mission_progress is not None
        else None,
        "mission_start_time": _iso(_as_utc(latest.mission_start_time)),
        "last_seen": _iso(ts),
        "runtime_remaining_minutes": _estimate_runtime_minutes(rows, ts),
        "x": round(latest.x, 2) if latest.x is not None else 0.0,
        "y": round(latest.y, 2) if latest.y is not None else 0.0,
        "battery_health": round(latest.battery_health, 2) if latest.battery_health else 100.0,
        "motor_health": round(latest.motor_health, 2) if latest.motor_health else 100.0,
        "sensor_health": round(latest.sensor_health, 2) if latest.sensor_health else 100.0,
        "network_health": round(latest.network_health, 2) if latest.network_health else 100.0,
    }


def summarize_silent_robot(robot: Robot) -> dict:
    """Build a status payload for a registered robot with no recent telemetry.

    Telemetry-derived fields are zeroed rather than guessed: the last reading
    is outside the query window, and showing a stale battery percentage next to
    an OFFLINE badge invites an operator to trust a number that may be hours
    old. ``last_seen`` carries the roster's record of when it was last heard
    from, which is the only fact still known about it.
    """
    return {
        "robot_id": robot.id,
        "battery": 0.0,
        "temperature": 0.0,
        "speed": 0.0,
        "status": OFFLINE,
        "mission_id": None,
        "mission_type": None,
        "mission_progress": None,
        "mission_start_time": None,
        "last_seen": _iso(_as_utc(robot.last_seen)),
        "runtime_remaining_minutes": None,
        "x": 0.0,
        "y": 0.0,
        "battery_health": 0.0,
        "motor_health": 0.0,
        "sensor_health": 0.0,
        "network_health": 0.0,
    }


class RobotService:
    """Derives current fleet status from the roster plus recent telemetry."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)
        self.robots = RobotRepository(db)

    async def get_fleet_status(self, limit: int = 50, skip: int = 0) -> list[dict]:
        """Return the current status summary for every registered robot.

        Cached briefly to keep repeated dashboard polls off the database.
        """
        cache_key = f"fleet_status_summary_{limit}_{skip}"
        use_cache = settings.opt_read_cache

        if use_cache:
            cached = await cache.get(cache_key)
            if cached is not None:
                return cached

        registered = await self.robots.list_active()
        grouped = await self.repo.get_recent_per_robot(
            per_robot_limit=settings.fleet_history_per_robot,
            window_minutes=settings.fleet_window_minutes,
        )

        summaries: dict[int, dict] = {}

        for robot in registered:
            rows = grouped.get(robot.id)
            summary = summarize_robot_history(rows) if rows else None
            summaries[robot.id] = summary or summarize_silent_robot(robot)

        # A robot reporting telemetry before the worker has registered it would
        # otherwise be invisible for one batch. Include it rather than drop it.
        for robot_id, rows in grouped.items():
            if robot_id not in summaries:
                summary = summarize_robot_history(rows)
                if summary is not None:
                    summaries[robot_id] = summary

        robots = [summaries[robot_id] for robot_id in sorted(summaries)]
        offline = sum(1 for r in robots if r["status"] == OFFLINE)
        logger.debug("Fleet status computed for %d robots (%d offline)", len(robots), offline)

        paginated = robots[skip : skip + limit]

        if use_cache:
            await cache.set(cache_key, paginated, ttl_seconds=10.0)
        return paginated
