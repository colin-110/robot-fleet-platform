"""Storing each field as straight-line segments with a guaranteed error.

What is stored today is the shared prediction: from each keyframe, a value
carried forward by its rate (position, battery) or held (the rest), until the
next keyframe or the heartbeat, whichever comes first (see ``rollups``). That
path is a chain of straight pieces, one per keyframe, and every field is
written in every row whenever any one of them changes, so a battery that
drains steadily for an hour is repeated in hundreds of rows.

Here each field gets its own segments instead: one line, ``v0 + slope * (t -
t0)``, for as long as it stays within ``tolerance`` of the stored path, then a
new line starting from the path's real value (re-anchoring, so error never
builds up from one segment to the next).

The guarantee holds at every instant, not only at stored points. The path and
a segment are both straight over each keyframe's stretch, so the gap between
them is largest at the stretch's two ends; the fit checks both: the keyframe's
value, and where its prediction stood just before the next keyframe. The fit
is the swing filter: it keeps the range of slopes that pass within tolerance
of every point so far, and closes the segment when that range empties.

Time with no keyframe in force (past the heartbeat with its grace, or before
the first) is not covered by any segment: unknown, never guessed.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

# Fields stored as segments, with their rate on the keyframe (None: held).
FIELDS = {
    "x": "vx",
    "y": "vy",
    "battery": "battery_rate",
    "temperature": None,
    "speed": None,
    "mission_progress": None,
    "battery_health": None,
    "motor_health": None,
    "sensor_health": None,
    "network_health": None,
}
# Small integer ids for the table (stable: never renumber).
FIELD_IDS = {name: i for i, name in enumerate(FIELDS)}
# Fields that are not numbers: stored as their changes (``changes``), not lines.
STATE_FIELDS = ("status", "mission_id", "mission_type", "mission_start_time")
STATE_IDS = {name: i for i, name in enumerate(STATE_FIELDS)}


@dataclass(frozen=True)
class Line:
    """``value(t) = v0 + slope * (t - t0)`` for ``t0 <= t <= t1``."""

    t0: float
    t1: float
    v0: float
    slope: float

    def value(self, t: float) -> float:
        return self.v0 + self.slope * (t - self.t0)


# A keyframe stays in force past the heartbeat by this factor, like silence
# detection (app/intelligence/fleet.py): a robot sends its heartbeat on the
# first sensor tick after the interval, so the next keyframe routinely comes a
# tick or two late, and that is not a gap in what is known.
HEARTBEAT_GRACE = 1.5


def pieces(keyframes: list[dict], name: str, end: float, heartbeat_s: float) -> list[tuple]:
    """The stored path of one field as straight pieces ``(ta, va, tb, vb)``.

    One per keyframe: its value carried forward by its rate (or held) until
    the next keyframe, the heartbeat (with grace) or ``end``, whichever is
    first. The battery prediction stops at 0 and 100, so a piece that would
    cross one is split there and held.
    """
    rate_name = FIELDS[name]
    cover = heartbeat_s * HEARTBEAT_GRACE
    out = []
    for i, kf in enumerate(keyframes):
        value = kf.get(name)
        t0 = kf["timestamp"]
        nxt = keyframes[i + 1]["timestamp"] if i + 1 < len(keyframes) else end
        t1 = min(nxt, t0 + cover, end)
        if value is None or t1 <= t0:
            continue
        rate = (kf.get(rate_name) or 0.0) if rate_name else 0.0
        bend = _battery_bend(value, rate, t0, t1) if name == "battery" else None
        if bend is None:
            out.append((t0, value, t1, value + rate * (t1 - t0)))
        else:
            held = min(100.0, max(0.0, value + rate * (bend - t0)))
            out.append((t0, value, bend, held))
            out.append((bend, held, t1, held))
    return out


def path(
    keyframes: list[dict], name: str, end: float, heartbeat_s: float, start: float | None = None
) -> list[list]:
    """The stored path of one field over ``[start, end]``, as runs of
    ``(t, value)`` points: each piece's two ends, clipped to the window. A run
    breaks wherever the field is unknown (no keyframe in force, or missing).
    """
    runs, run = [], []
    for ta, va, tb, vb in pieces(keyframes, name, end, heartbeat_s):
        if start is not None:
            if tb <= start:
                continue
            if ta < start:
                va = va + (vb - va) * (start - ta) / (tb - ta)
                ta = start
        if run and run[-1][0] < ta - 1e-9:
            runs.append(run)
            run = []
        run.append((ta, va))
        run.append((tb, vb))
    if run:
        runs.append(run)
    return runs


def _battery_bend(value: float, rate: float, t0: float, t1: float) -> float | None:
    if not rate:
        return None
    for bound in (0.0, 100.0):
        t = t0 + (bound - value) / rate
        if t0 < t < t1:
            return t
    return None


def fit(points: list[tuple[float, float]], tolerance: float) -> list[Line]:
    """Segments through ``points`` (sorted by time; equal times allowed, which
    is a jump) each within ``tolerance`` of every point it covers.

    Each segment starts at a real point's value and takes the slope nearest
    zero within its allowed range, so a field that holds still is stored as a
    flat line.
    """
    lines: list[Line] = []
    i, n = 0, len(points)
    while i < n:
        t0, v0 = points[i]
        lo, hi = float("-inf"), float("inf")
        j = i + 1
        while j < n:
            t, v = points[j]
            dt = t - t0
            if dt <= 0:
                # Same instant as the anchor: fine only within tolerance.
                if abs(v - v0) > tolerance:
                    break
                j += 1
                continue
            new_lo = max(lo, (v - tolerance - v0) / dt)
            new_hi = min(hi, (v + tolerance - v0) / dt)
            if new_lo > new_hi:
                break
            lo, hi = new_lo, new_hi
            j += 1
        slope = 0.0 if lo == float("-inf") else min(max(0.0, lo), hi)
        last = points[j - 1][0]
        lines.append(Line(t0, last, v0, slope))
        # The next segment starts where this one ended, from the real value
        # there, unless that is the point that did not fit.
        i = j - 1 if j < n and j - 1 > i and points[j][0] > last else j
    return lines


def lines_for(
    keyframes: list[dict],
    name: str,
    end: float,
    heartbeat_s: float,
    tolerance: float,
    start: float | None = None,
) -> list[Line]:
    """One field's segments over the keyframes, within ``[start, end]``."""
    out: list[Line] = []
    for run in path(keyframes, name, end, heartbeat_s, start):
        out.extend(fit(run, tolerance))
    return out


def worst_error(lines: list[Line], runs: list[list]) -> float:
    """The largest gap between the segments and the stored path, checked at
    every point of it (enough, since both are straight between points).
    ``inf`` if a known point has no segment. ``lines`` sorted by ``t0``."""
    starts = [ln.t0 for ln in lines]
    worst = 0.0
    for run in runs:
        for t, v in run:
            i = bisect.bisect_right(starts, t + 1e-9) - 1
            # At a boundary the segment before may also claim the instant; a
            # jump there is shared, so the closer one is the stored value.
            covering = [
                lines[k]
                for k in (i - 1, i)
                if k >= 0 and lines[k].t0 - 1e-9 <= t <= lines[k].t1 + 1e-9
            ]
            if not covering:
                return float("inf")
            worst = max(worst, min(abs(ln.value(t) - v) for ln in covering))
    return worst


def changes(
    keyframes: list[dict], name: str, end: float, heartbeat_s: float, start: float
) -> list[tuple[float, str | None]]:
    """A non-numeric field over ``[start, end)`` as ``(t, value)`` changes.

    The value in force at ``start`` comes first, so every window stands on its
    own; then each change; ``None`` marks where it becomes unknown (no
    keyframe in force, by the same heartbeat rule as the lines).
    """
    cover = heartbeat_s * HEARTBEAT_GRACE
    out: list[tuple[float, str | None]] = []
    current: object = object()  # nothing recorded yet
    for i, kf in enumerate(keyframes):
        t0 = kf["timestamp"]
        nxt = keyframes[i + 1]["timestamp"] if i + 1 < len(keyframes) else end
        t1 = min(nxt, t0 + cover, end)
        if t1 <= start or t0 >= end:
            continue
        value = kf.get(name)
        value = None if value is None else str(value)
        at = max(t0, start)
        if value != current:
            out.append((at, value))
            current = value
        if t1 < nxt and t1 < end and current is not None:
            # Past the heartbeat with no new keyframe: unknown from here.
            out.append((t1, None))
            current = None
    return out
