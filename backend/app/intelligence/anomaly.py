"""Anomalies relative to fleet peers: robust z-scores.

A robot is compared with the rest of the fleet, not with a fixed limit: 48 °C
is normal on a hot day when every robot reads 47, and alarming when the rest
read 35. Each feature is scored as ``(x - median) / (1.4826 * MAD)``, which
is a z-score for normal data but is not dragged by the outliers it is looking
for, as a mean and standard deviation would be. The spread has a floor so a
fleet that reads almost identically does not flag tiny differences.

Features: temperature, and battery drain (percent per minute, from the
Kalman filter). Drain is compared only among robots that are moving: an idle
robot drains a fraction of what a driving one does, and comparing the two
flags whichever is in the minority, which says nothing about either robot.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

MIN_PEERS = 5
# Readings older than this do not take part.
MAX_AGE_SECONDS = 120.0
FLOORS = {"temperature": 1.0, "drain": 0.1}
# m/s; below this a robot is not doing comparable work for drain.
MOVING_SPEED = 0.3


@dataclass(frozen=True)
class Anomaly:
    score: float  # the largest |z| over the features
    feature: str
    z: float


def _features(model) -> dict[str, float]:
    out = {}
    temperature = model.last.get("temperature")
    if temperature is not None:
        out["temperature"] = temperature
    moving = (model.last.get("speed") or 0.0) >= MOVING_SPEED
    if moving and model.battery.t is not None and model.battery.rate < 0:
        out["drain"] = -model.battery.rate * 60
    return out


def scores(models: dict, now: float) -> dict[int, Anomaly]:
    """The anomaly score of every robot with recent readings."""
    fresh = {
        robot_id: _features(model)
        for robot_id, model in models.items()
        if now - model.last.get("t", 0.0) <= MAX_AGE_SECONDS
    }
    result: dict[int, Anomaly] = {}
    for feature, floor in FLOORS.items():
        values = {r: f[feature] for r, f in fresh.items() if feature in f}
        if len(values) < MIN_PEERS:
            continue
        median = statistics.median(values.values())
        mad = statistics.median(abs(v - median) for v in values.values())
        scale = max(1.4826 * mad, floor)
        for robot_id, value in values.items():
            z = (value - median) / scale
            best = result.get(robot_id)
            if best is None or abs(z) > best.score:
                result[robot_id] = Anomaly(abs(z), feature, z)
    return result
