"""
Robot service - fleet status derivation.

Fleet status is the roster LEFT JOINed onto each robot's latest state, not a
GROUP BY over recent telemetry. The distinction matters: deriving the fleet from
telemetry alone means a robot that stops reporting eventually falls outside the
query window and silently disappears from the dashboard. A monitoring system
that quietly forgets a unit it can no longer see has failed at its one job, so
every registered robot is always listed and missing telemetry renders OFFLINE.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app import presence
from app.config import get_settings
from app.models import Robot
from app.mqtt import predict
from app.repositories.config_repo import applied_settings
from app.repositories.robot_repo import RobotRepository

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


def _derive_status(latest, age_seconds: float, silent_after: float | None = None) -> str:
    """Report the robot's own status, unless it has gone quiet.

    One threshold, from configuration. This previously checked three different
    values in two places - ``>300`` and ``>60`` here and ``>180`` in the
    caller - of which ``>300`` was unreachable and ``>180`` was dead for any
    robot that reported a status.
    """
    # The broker's last will is immediate; silence is the fallback for robots
    # on HTTP or when the broker is unreachable.
    limit = settings.offline_after_seconds if silent_after is None else silent_after
    if presence.is_offline(latest.robot_id) or age_seconds > limit:
        return OFFLINE
    return latest.status.upper() if latest.status else "ACTIVE"


def _health(value: float | None) -> float:
    """Component health, defaulting only when the device reported nothing.

    The distinction is null versus zero, and a truthiness check collapses them:
    ``value if value else 100.0`` renders a completely failed component - 0.0,
    the single most urgent reading the system can receive - as perfect health.
    That inverts the signal rather than losing it, which is the one failure mode
    a monitoring system must not have.

    Absent readings still default to 100.0: older devices predate per-component
    reporting, and showing them at zero would flag healthy units as dead.
    """
    return round(value, 2) if value is not None else 100.0


def _estimate_runtime_minutes(state, battery: float | None = None) -> float | None:
    """Remaining runtime from the drain rate.

    The robot's own rate from its keyframe when it sent one, else the
    server's smoothed estimate. None when there is no rate yet or the battery
    is not falling: a flat or charging battery has no meaningful time-to-empty.
    """
    rate = getattr(state, "battery_rate", None)
    drain = -rate * 60 if rate is not None else getattr(state, "battery_drain_per_min", None)
    if drain is None or drain <= 0:
        return None
    battery = state.battery if battery is None else battery
    effective_battery = battery * ((state.battery_health or 100.0) / 100.0)
    return round(effective_battery / drain, 1)


def _carried_forward(state, ts: datetime, now: datetime, tol: dict) -> dict | None:
    """x, y and battery predicted for now from the keyframe in ``state``.

    None when the keyframe has no rates (a robot that sends every reading).
    Prediction stops at the heartbeat: by then a keyframe is overdue, the
    robot may be gone, and the bound no longer holds.
    """
    rates = {name: getattr(state, name, None) for name in ("vx", "vy", "battery_rate")}
    if all(value is None for value in rates.values()):
        return None
    keyframe = {
        "timestamp": ts.timestamp(),
        "x": state.x,
        "y": state.y,
        "battery": state.battery,
        **rates,
    }
    until = min(now.timestamp(), ts.timestamp() + tol["heartbeat_s"])
    return predict.predict(keyframe, until)


def summarize_state(state, applied: dict | None = None) -> dict:
    """Turn a robot's latest state into a status payload.

    ``applied`` is the settings the robot reported applying; its tolerances
    and heartbeat decide the error bounds and how long silence is normal.
    """
    now = datetime.now(timezone.utc)
    ts = _as_utc(state.timestamp)
    age_seconds = (now - ts).total_seconds()
    tol = predict.tolerances(applied)
    # A robot on a 30 s heartbeat is not offline after 20 s of silence.
    silent_after = max(settings.offline_after_seconds, 2 * tol["heartbeat_s"])
    status = _derive_status(state, age_seconds, silent_after)
    carried = _carried_forward(state, ts, now, tol) if status != OFFLINE else None
    x, y, battery = (
        (carried["x"], carried["y"], carried["battery"])
        if carried
        else (state.x, state.y, state.battery)
    )

    return {
        "robot_id": state.robot_id,
        "battery": round(battery, 2),
        "temperature": round(state.temperature, 2),
        "speed": round(state.speed, 2),
        "status": status,
        "mission_id": state.mission_id,
        "mission_type": state.mission_type,
        "mission_progress": round(state.mission_progress, 1)
        if state.mission_progress is not None
        else None,
        "mission_start_time": _iso(_as_utc(state.mission_start_time)),
        "last_seen": _iso(ts),
        "runtime_remaining_minutes": _estimate_runtime_minutes(state, battery),
        "x": round(x, 2) if x is not None else 0.0,
        "y": round(y, 2) if y is not None else 0.0,
        "vx": getattr(state, "vx", None),
        "vy": getattr(state, "vy", None),
        "battery_rate": getattr(state, "battery_rate", None),
        "predicted": carried is not None,
        "keyframe_at": _iso(ts),
        "bounds": predict.bounds(tol) if carried else None,
        "battery_health": _health(state.battery_health),
        "motor_health": _health(state.motor_health),
        "sensor_health": _health(state.sensor_health),
        "network_health": _health(state.network_health),
    }


def summarize_silent_robot(robot: Robot) -> dict:
    """Build a status payload for a registered robot with no recent telemetry.

    Telemetry-derived fields are zeroed rather than guessed: showing a stale
    battery percentage next to an OFFLINE badge invites an operator to trust a
    number that may be hours old. ``last_seen`` carries the roster's record of
    when it was last heard from, which is the only fact still known about it.
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
    """Fleet status: the roster joined with each robot's latest state."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.robots = RobotRepository(db)

    async def get_fleet_status(self, limit: int = 50, skip: int = 0) -> list[dict]:
        """Return the current status summary for every registered robot.

        One row per robot from robot_state, so the cost does not depend on how
        much telemetry history exists. A robot whose latest reading is older
        than the fleet window is listed as silent rather than with stale values.
        """
        window = settings.fleet_window_minutes * 60
        now = datetime.now(timezone.utc)
        robots = []
        applied = await applied_settings(self.db)
        for robot, state in await self.robots.list_with_state(limit=limit, skip=skip):
            fresh = state is not None and (now - _as_utc(state.timestamp)).total_seconds() <= window
            robots.append(
                summarize_state(state, applied.get(robot.id))
                if fresh
                else summarize_silent_robot(robot)
            )
        return robots
