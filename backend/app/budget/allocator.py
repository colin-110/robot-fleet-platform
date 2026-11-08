"""Choosing each robot's tolerance to meet a fleet message budget.

Every robot's tolerances are its defaults times a scale ``s``. With dual
prediction, a robot's keyframe rate falls with the square of its tolerance
(a value that wanders like a random walk takes ``s**2`` times as long to
stray ``s`` times as far), on top of a floor its heartbeat sets::

    rate_i(s) = c_i / s**2 + h_i          (keyframes per minute)

``c_i`` is learned from the robot itself: its observed rate at its current
scale, less the heartbeat floor, times the scale squared.

Given a budget ``B`` and a weight ``w_i`` per robot (higher for one with an
alarm or an anomaly), choose the scales that minimise the weighted error::

    minimise   sum  w_i * s_i
    subject to sum  c_i / s_i**2  <=  B - sum h_i

Setting the Lagrangian's derivative to zero gives ``w_i = 2 * lam * c_i / s_i**3``,
so ``s_i = (2 * lam * c_i / w_i) ** (1/3)``: a robot gets a tighter tolerance
the more it matters and the cheaper it is to watch closely. ``lam`` is found
by bisection so the budget is met, and scales are clamped to [MIN, MAX].
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_SCALE = 0.25
MAX_SCALE = 8.0
# A robot with no measurable demand still gets a little, so it is not set
# to the loosest tolerance just for being quiet a while.
MIN_DEMAND = 0.05


@dataclass(frozen=True)
class Demand:
    robot_id: int
    rate_per_min: float  # keyframes per minute observed
    scale: float  # the scale in force while that rate was observed
    weight: float = 1.0
    floor_per_min: float = 2.0  # the heartbeat's share: 60 / heartbeat_s

    @property
    def c(self) -> float:
        return max(self.rate_per_min - self.floor_per_min, MIN_DEMAND) * self.scale**2


def _scales(demands: list[Demand], lam: float) -> dict[int, float]:
    return {
        d.robot_id: min(MAX_SCALE, max(MIN_SCALE, (2 * lam * d.c / d.weight) ** (1 / 3)))
        for d in demands
    }


def predicted_rate(demands: list[Demand], scales: dict[int, float]) -> float:
    """Keyframes per minute the fleet would send at these scales."""
    return sum(d.c / scales[d.robot_id] ** 2 + d.floor_per_min for d in demands)


def allocate(demands: list[Demand], budget_per_min: float) -> dict[int, float]:
    """The scale for each robot. Loosest everywhere if the budget cannot be met."""
    if not demands:
        return {}
    if predicted_rate(demands, _scales(demands, 0.0)) <= budget_per_min:
        return _scales(demands, 0.0)  # everyone at the tightest scale fits
    lo, hi = 0.0, 1.0
    while predicted_rate(demands, _scales(demands, hi)) > budget_per_min:
        hi *= 4
        if hi > 1e12:
            return {d.robot_id: MAX_SCALE for d in demands}
    for _ in range(60):
        mid = (lo + hi) / 2
        if predicted_rate(demands, _scales(demands, mid)) > budget_per_min:
            lo = mid
        else:
            hi = mid
    return _scales(demands, hi)
