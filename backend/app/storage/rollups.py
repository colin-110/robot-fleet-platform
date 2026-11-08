"""Exact time-weighted rollups from keyframe segments.

Storage holds keyframes, which are unevenly spaced by design, so ``AVG()``
over rows is wrong: a minute with one keyframe and a minute with twenty would
weigh the same. Between keyframes the value is known exactly, though: it is
the shared prediction (``app/mqtt/predict.py``), a straight line for battery
and position and held values for the rest, until the next keyframe or the
heartbeat, whichever comes first. Past the heartbeat a keyframe was overdue,
so that time counts as not covered rather than guessed.

So each bucket's average is an integral over those segments divided by the
time they cover, which reproduces what the dashboard showed at every instant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

HELD_FIELDS = (
    "temperature",
    "speed",
    "battery_health",
    "motor_health",
    "sensor_health",
    "network_health",
)


@dataclass
class Segment:
    """A keyframe's stretch of time: ``[t0, t1)`` in epoch seconds."""

    t0: float
    t1: float
    battery: float | None
    battery_rate: float
    vx: float
    vy: float
    held: dict


def segments(keyframes: list[dict], end: float, heartbeat_s: float) -> list[Segment]:
    """One segment per keyframe (sorted by ``timestamp``), up to ``end``."""
    out = []
    for i, kf in enumerate(keyframes):
        nxt = keyframes[i + 1]["timestamp"] if i + 1 < len(keyframes) else end
        t1 = min(nxt, kf["timestamp"] + heartbeat_s, end)
        if t1 <= kf["timestamp"]:
            continue
        out.append(
            Segment(
                kf["timestamp"],
                t1,
                kf.get("battery"),
                kf.get("battery_rate") or 0.0,
                kf.get("vx") or 0.0,
                kf.get("vy") or 0.0,
                {name: kf.get(name) for name in HELD_FIELDS},
            )
        )
    return out


def _battery_pieces(seg: Segment, a: float, b: float):
    """``(t_start, t_end, v_start, v_end)`` of the battery line on ``[a, b)``,
    split where it meets 0 or 100, beyond which the prediction is clamped."""

    def value(t):
        return seg.battery + seg.battery_rate * (t - seg.t0)

    cuts = [a, b]
    if seg.battery_rate:
        for bound in (0.0, 100.0):
            t = seg.t0 + (bound - seg.battery) / seg.battery_rate
            if a < t < b:
                cuts.append(t)
    cuts.sort()
    for start, stop in zip(cuts, cuts[1:], strict=False):
        v0, v1 = value(start), value(stop)
        mid = value((start + stop) / 2)
        if mid > 100 or mid < 0:
            v0 = v1 = min(100.0, max(0.0, mid))
        yield start, stop, v0, v1


@dataclass
class Bucket:
    start: float
    covered: float = 0.0
    keyframes: int = 0
    distance_m: float = 0.0
    battery_area: float = 0.0
    battery_covered: float = 0.0
    battery_min: float | None = None
    battery_max: float | None = None
    held_area: dict = field(default_factory=dict)
    held_covered: dict = field(default_factory=dict)
    temperature_max: float | None = None

    def add(self, seg: Segment, a: float, b: float) -> None:
        span = b - a
        self.covered += span
        self.distance_m += math.hypot(seg.vx, seg.vy) * span
        if seg.battery is not None:
            for start, stop, v0, v1 in _battery_pieces(seg, a, b):
                self.battery_area += (v0 + v1) / 2 * (stop - start)
                self.battery_covered += stop - start
                lo, hi = min(v0, v1), max(v0, v1)
                self.battery_min = lo if self.battery_min is None else min(self.battery_min, lo)
                self.battery_max = hi if self.battery_max is None else max(self.battery_max, hi)
        for name, value in seg.held.items():
            if value is None:
                continue
            self.held_area[name] = self.held_area.get(name, 0.0) + value * span
            self.held_covered[name] = self.held_covered.get(name, 0.0) + span
        t = seg.held.get("temperature")
        if t is not None:
            self.temperature_max = (
                t if self.temperature_max is None else max(self.temperature_max, t)
            )

    def row(self, robot_id: int) -> dict:
        def avg(name):
            covered = self.held_covered.get(name)
            return self.held_area[name] / covered if covered else None

        return {
            "robot_id": robot_id,
            "seconds_covered": self.covered,
            "keyframes": self.keyframes,
            "distance_m": self.distance_m,
            "battery_avg": self.battery_area / self.battery_covered
            if self.battery_covered
            else None,
            "battery_min": self.battery_min,
            "battery_max": self.battery_max,
            "temperature_avg": avg("temperature"),
            "temperature_max": self.temperature_max,
            "speed_avg": avg("speed"),
            "battery_health_avg": avg("battery_health"),
            "motor_health_avg": avg("motor_health"),
            "sensor_health_avg": avg("sensor_health"),
            "network_health_avg": avg("network_health"),
        }


def minute_buckets(
    keyframes: list[dict], start: float, end: float, heartbeat_s: float, width: float = 60.0
) -> dict[float, Bucket]:
    """Buckets of ``width`` seconds from ``start`` to ``end`` (both on bucket
    boundaries). ``keyframes`` must include the one in force at ``start``."""
    buckets: dict[float, Bucket] = {}
    for kf in keyframes:
        if start <= kf["timestamp"] < end:
            b0 = start + (kf["timestamp"] - start) // width * width
            buckets.setdefault(b0, Bucket(b0)).keyframes += 1
    for seg in segments(keyframes, end, heartbeat_s):
        a = max(seg.t0, start)
        while a < seg.t1:
            b0 = start + (a - start) // width * width
            b = min(seg.t1, b0 + width)
            buckets.setdefault(b0, Bucket(b0)).add(seg, a, b)
            a = b
    return {k: v for k, v in buckets.items() if v.covered > 0 or v.keyframes}
