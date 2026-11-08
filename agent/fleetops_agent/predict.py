"""The predictor robots and the server share (dual prediction).

The robot sends a keyframe (its current values and rates) and both sides then
compute the same prediction from it::

    position   x(t) = x0 + vx * (t - t0),   y(t) = y0 + vy * (t - t0)
    battery    b(t) = clamp(b0 + battery_rate * (t - t0), 0, 100)
    the rest   held at the keyframe value

On every sensor tick the robot compares what it measures with that
prediction, and sends a new keyframe only when the error passes a tolerance,
a discrete field changes, or the heartbeat interval passes. So between
keyframes the server's value is within the tolerance of what the robot
measured at each tick, and the server knows the bound.

Both sides must predict from the same numbers, so the robot predicts from the
keyframe as it will arrive (packed and unpacked through the wire format), not
from its unrounded values; otherwise rounding of a rate would add up to drift
the robot cannot see.

This module is copied to ``backend/app/mqtt/predict.py`` and to the dashboard
(``frontend/.../utils/predict.js``); ``tests/predict_vectors.json`` checks
that all three agree.
"""

from __future__ import annotations

import math

# value -> its rate, per second
LINEAR = {"x": "vx", "y": "vy", "battery": "battery_rate"}
# Compared with a tolerance; held between keyframes.
HELD = {
    "temperature": "tol_temperature_c",
    "speed": "tol_speed",
    "mission_progress": "tol_progress_pct",
    "battery_health": "tol_health_pct",
    "motor_health": "tol_health_pct",
    "sensor_health": "tol_health_pct",
    "network_health": "tol_health_pct",
}
# Any change is sent at once.
DISCRETE = ("status", "mission_id", "mission_type", "mission_start_time")

DEFAULTS = {
    "tol_position_m": 0.5,
    "tol_battery_pct": 1.0,
    "tol_temperature_c": 1.0,
    "tol_speed": 0.3,
    "tol_progress_pct": 5.0,
    "tol_health_pct": 2.0,
    "heartbeat_s": 30.0,
}


def tolerances(config: dict | None = None) -> dict:
    """The defaults overridden by whichever tolerances ``config`` sets."""
    merged = dict(DEFAULTS)
    for key, value in (config or {}).items():
        if key in DEFAULTS and value is not None:
            merged[key] = float(value)
    return merged


def predict(keyframe: dict, t: float) -> dict:
    """The keyframe's values carried forward to time ``t`` (epoch seconds).

    Times before the keyframe return it unchanged.
    """
    dt = max(0.0, t - keyframe["timestamp"])
    out = dict(keyframe)
    out["timestamp"] = max(t, keyframe["timestamp"])
    for name, rate in LINEAR.items():
        value = keyframe.get(name)
        if value is not None:
            out[name] = value + (keyframe.get(rate) or 0.0) * dt
    if out.get("battery") is not None:
        out["battery"] = min(100.0, max(0.0, out["battery"]))
    return out


def keyframe_reason(keyframe: dict | None, reading: dict, tol: dict) -> str | None:
    """Why ``reading`` must be sent as a new keyframe, or None if it need not be.

    ``reading`` is what the robot measures now; ``keyframe`` is the last one
    sent, as the server sees it. ``tol`` is a full tolerance table.
    """
    if keyframe is None:
        return "first"
    t = reading["timestamp"]
    if t - keyframe["timestamp"] >= tol["heartbeat_s"]:
        return "heartbeat"
    for name in DISCRETE:
        if reading.get(name) != keyframe.get(name):
            return name
    expected = predict(keyframe, t)
    if _missing_changed(expected, reading, ("x", "y", "battery", *HELD)):
        return "field"
    if reading.get("x") is not None and reading.get("y") is not None:
        error = math.hypot(reading["x"] - expected["x"], reading["y"] - expected["y"])
        if error > tol["tol_position_m"]:
            return "position"
    if (
        reading.get("battery") is not None
        and abs(reading["battery"] - expected["battery"]) > tol["tol_battery_pct"]
    ):
        return "battery"
    for name, key in HELD.items():
        value = reading.get(name)
        if value is not None and abs(value - expected[name]) > tol[key]:
            return name
    return None


def _missing_changed(expected: dict, reading: dict, names) -> bool:
    """A value that appeared or disappeared since the keyframe."""
    return any((expected.get(n) is None) != (reading.get(n) is None) for n in names)


def bounds(tol: dict) -> dict:
    """The error bound of each predicted value, for display."""
    return {
        "position_m": tol["tol_position_m"],
        "battery_pct": tol["tol_battery_pct"],
        "temperature_c": tol["tol_temperature_c"],
        "speed": tol["tol_speed"],
        "mission_progress_pct": tol["tol_progress_pct"],
        "health_pct": tol["tol_health_pct"],
        # Prediction stops here, so clients carrying values forward know when.
        "heartbeat_s": tol["heartbeat_s"],
    }


class RateEstimator:
    """Velocity and battery drain from successive readings, for robots that
    do not report them.

    An exponentially weighted average of the per-second change, light for
    position (odometry is smooth, and a lagging velocity after every turn costs
    a run of keyframes) and heavy for the battery (its readings are noisy and
    its true rate changes slowly). A battery that jumps up (charging, a swap)
    restarts its estimate instead of averaging across the jump.
    """

    def __init__(self, position_alpha: float = 0.8, battery_alpha: float = 0.2) -> None:
        self.alphas = {"vx": position_alpha, "vy": position_alpha, "battery_rate": battery_alpha}
        self._last: dict | None = None
        self._rates: dict[str, float] = {}

    def update(self, reading: dict) -> dict:
        """``reading`` with ``vx``, ``vy`` and ``battery_rate`` filled in where missing."""
        out = dict(reading)
        last = self._last
        if last is not None:
            dt = reading["timestamp"] - last["timestamp"]
            if dt > 0:
                for name, rate in LINEAR.items():
                    now, before = reading.get(name), last.get(name)
                    if now is None or before is None:
                        self._rates.pop(rate, None)
                        continue
                    instant = (now - before) / dt
                    jumped = name == "battery" and now > before + 0.5
                    previous = self._rates.get(rate)
                    alpha = self.alphas[rate]
                    self._rates[rate] = (
                        instant
                        if previous is None or jumped
                        else alpha * instant + (1 - alpha) * previous
                    )
        self._last = reading
        for rate in LINEAR.values():
            if out.get(rate) is None and rate in self._rates:
                out[rate] = self._rates[rate]
        return out
