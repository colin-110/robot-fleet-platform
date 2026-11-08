"""What the API knows about each robot right now, kept up to date from every
stored reading. One process holds it in memory; it is rebuilt from new
readings after a restart."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.intelligence.kalman import BatteryKalman
from app.intelligence.phi import PhiAccrual


@dataclass
class RobotModel:
    battery: BatteryKalman = field(default_factory=BatteryKalman)
    arrivals: PhiAccrual = field(default_factory=PhiAccrual)
    last: dict = field(default_factory=dict)


_robots: dict[int, RobotModel] = {}


def observe(rows, now: float | None = None) -> None:
    """Fold newly stored telemetry rows in, oldest first.

    Arrivals are timed by the server clock, once per robot per batch.
    """
    now = time.time() if now is None else now
    for robot_id in {row.robot_id for row in rows}:
        _robots.setdefault(robot_id, RobotModel()).arrivals.heartbeat(now)
    for row in sorted(rows, key=lambda r: r.timestamp):
        model = _robots.setdefault(row.robot_id, RobotModel())
        t = row.timestamp.timestamp()
        model.battery.update(t, row.battery, getattr(row, "battery_rate", None))
        if t >= model.last.get("t", 0.0):
            model.last = {
                "t": t,
                "temperature": row.temperature,
                "battery": row.battery,
                "health": min(
                    (
                        h
                        for h in (
                            row.battery_health,
                            row.motor_health,
                            row.sensor_health,
                            row.network_health,
                        )
                        if h is not None
                    ),
                    default=None,
                ),
            }


def suspicion(robot_id: int, now: float | None = None) -> float | None:
    model = _robots.get(robot_id)
    return model.arrivals.phi(time.time() if now is None else now) if model else None


def get(robot_id: int) -> RobotModel | None:
    return _robots.get(robot_id)


def robots() -> dict[int, RobotModel]:
    return _robots


def reset() -> None:
    _robots.clear()
