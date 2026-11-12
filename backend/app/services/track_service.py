"""Reconstructing a robot's path from its keyframes.

Storage holds keyframes only. Between two of them the value at any instant
is the shared prediction from the earlier one, which is exactly what the
dashboard showed at that instant and within the robot's tolerance of what it
measured. As on the dashboard, prediction stops at the heartbeat: past it a
keyframe was overdue and the value is held, not extrapolated.
"""

import bisect
from datetime import datetime, timedelta, timezone

from app.models import Telemetry
from app.mqtt import predict

MAX_POINTS = 2000


def _keyframe(row: Telemetry) -> dict:
    return {
        "timestamp": row.timestamp.timestamp(),
        "x": row.x,
        "y": row.y,
        "battery": row.battery,
        "vx": row.vx,
        "vy": row.vy,
        "battery_rate": row.battery_rate,
    }


def reconstruct(
    rows: list[Telemetry], start: datetime, end: datetime, step_s: float, applied: dict | None
) -> list[dict]:
    """Points every ``step_s`` seconds from ``start`` to ``end``.

    ``keyframe`` marks a point whose step contains a stored keyframe. Points
    before the first keyframe are left out.
    """
    tol = predict.tolerances(applied)
    keyframes = [_keyframe(r) for r in rows]
    points = []
    index = -1
    t = start.timestamp()
    stop = end.timestamp()
    while t <= stop + 1e-9:
        while index + 1 < len(keyframes) and keyframes[index + 1]["timestamp"] <= t:
            index += 1
        if index >= 0:
            kf = keyframes[index]
            at = predict.predict(kf, min(t, kf["timestamp"] + tol["heartbeat_s"]))
            points.append(
                {
                    "timestamp": datetime.fromtimestamp(t, tz=timezone.utc),
                    "x": at["x"],
                    "y": at["y"],
                    "battery": at["battery"],
                    "keyframe": t - step_s < kf["timestamp"] <= t,
                }
            )
        t += step_s
    return points


def check_window(start: datetime, end: datetime, step_s: float) -> str | None:
    """Why the request is refused, or None."""
    if end <= start:
        return "end must be after start"
    if (end - start) / timedelta(seconds=step_s) > MAX_POINTS:
        return f"at most {MAX_POINTS} points; use a larger step"
    return None


def _value_at(segments: list[dict], starts: list[float], t: float) -> float | None:
    i = bisect.bisect_right(starts, t) - 1
    for k in (i, i - 1):
        if k >= 0:
            seg = segments[k]
            t0, t1 = seg["t0"].timestamp(), seg["t1"].timestamp()
            if t0 <= t <= t1:
                return seg["v0"] + seg["slope"] * (t - t0)
    return None


def from_segments(series: dict, start: datetime, end: datetime, step_s: float) -> list[dict]:
    """Points every ``step_s`` seconds from line segments (``series_service``),
    for windows whose raw keyframes have been dropped. Within each field's
    error bound; points where position is unknown are left out."""
    by_field = {name: s["segments"] for name, s in series.items()}
    starts = {name: [seg["t0"].timestamp() for seg in segs] for name, segs in by_field.items()}
    points = []
    t, stop = start.timestamp(), end.timestamp()
    while t <= stop + 1e-9:
        x = _value_at(by_field["x"], starts["x"], t)
        y = _value_at(by_field["y"], starts["y"], t)
        if x is not None and y is not None:
            battery = _value_at(by_field["battery"], starts["battery"], t)
            points.append(
                {
                    "timestamp": datetime.fromtimestamp(t, tz=timezone.utc),
                    "x": x,
                    "y": y,
                    "battery": battery,
                    "keyframe": False,
                }
            )
        t += step_s
    return points
