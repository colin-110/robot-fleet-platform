"""What the API knows about each robot right now, kept up to date from every
stored reading. One process holds it in memory; it is rebuilt from new
readings after a restart."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from app.intelligence.kalman import BatteryKalman
from app.intelligence.phi import PhiAccrual

RATE_SAMPLES = 1000


@dataclass
class RobotModel:
    battery: BatteryKalman = field(default_factory=BatteryKalman)
    arrivals: PhiAccrual = field(default_factory=PhiAccrual)
    last: dict = field(default_factory=dict)
    # Server arrival time of each recent keyframe, for the budget allocator.
    keyframes: deque = field(default_factory=lambda: deque(maxlen=RATE_SAMPLES))
    first_seen: float | None = None
    # The robot's drain while it was last moving, and when (anomaly.py): a
    # property of its battery that does not stop being true when it parks.
    moving_drain: tuple[float, float] | None = None

    def restart_rate(self, now: float) -> None:
        """Forget the keyframe rate, e.g. because the robot's tolerances changed."""
        self.keyframes.clear()
        self.first_seen = now

    def rate_per_minute(self, now: float, window: float = 120.0) -> float | None:
        """Keyframes per minute over the last ``window`` seconds (or since first
        seen); None until there is a minute of history."""
        if self.first_seen is None or now - self.first_seen < 60:
            return None
        span = min(window, now - self.first_seen)
        count = sum(1 for t in self.keyframes if t > now - span)
        return count * 60 / span


_robots: dict[int, RobotModel] = {}
# Server arrival time of every recent keyframe from any robot.
_arrivals: deque = deque(maxlen=100_000)


def observe(rows, now: float | None = None) -> None:
    """Fold newly stored telemetry rows in, oldest first.

    Arrivals are timed by the server clock, once per robot per batch.
    """
    now = time.time() if now is None else now
    for robot_id in {row.robot_id for row in rows}:
        model = _robots.setdefault(robot_id, RobotModel())
        model.arrivals.heartbeat(now)
        if model.first_seen is None:
            model.first_seen = now
    for row in rows:
        _robots[row.robot_id].keyframes.append(now)
        _arrivals.append(now)
    for row in sorted(rows, key=lambda r: r.timestamp):
        model = _robots.setdefault(row.robot_id, RobotModel())
        t = row.timestamp.timestamp()
        model.battery.update(t, row.battery, getattr(row, "battery_rate", None))
        if t >= model.last.get("t", 0.0):
            model.last = {
                "t": t,
                "temperature": row.temperature,
                "battery": row.battery,
                "speed": row.speed,
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


# Silence up to this many heartbeats is the robot keeping its word: under dual
# prediction it sends only when it must, and at least once per heartbeat.
HEARTBEAT_GRACE = 1.5


def suspicion(
    robot_id: int, now: float | None = None, heartbeat_s: float | None = None
) -> float | None:
    """Phi accrual for the robot's current silence (None until learned).

    With ``heartbeat_s``, silence within the heartbeat (and some grace for
    batching and the network) scores 0: a robot under dual prediction is
    quiet by design until then, however short its gaps were while it moved.
    """
    model = _robots.get(robot_id)
    if model is None:
        return None
    now = time.time() if now is None else now
    phi = model.arrivals.phi(now)
    if phi is None:
        return None
    last = model.arrivals.last
    if heartbeat_s is not None and last is not None and now - last <= heartbeat_s * HEARTBEAT_GRACE:
        return 0.0
    return phi


def total_rate(now: float, window: float = 120.0) -> float:
    """Keyframes per minute from the whole fleet over the last ``window`` seconds."""
    return sum(1 for t in _arrivals if t > now - window) * 60 / window


def get(robot_id: int) -> RobotModel | None:
    return _robots.get(robot_id)


def robots() -> dict[int, RobotModel]:
    return _robots


def forget(robot_id: int) -> None:
    """Drop a retired robot's model."""
    _robots.pop(robot_id, None)


def reset() -> None:
    _robots.clear()
    _arrivals.clear()
