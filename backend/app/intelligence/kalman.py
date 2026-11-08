"""Battery level and drain from a two-state Kalman filter.

State ``[b, r]``: the battery level in percent and its rate in percent per
second (negative while draining). Between readings the level moves by the
rate and both grow less certain; a reading pulls them back. A keyframe that
carries the robot's own rate is used as a second, noisier measurement.

Because the filter keeps its uncertainty, the remaining runtime comes with a
range: ``T = -b / r``, with the variance carried through to first order.
A level that jumps up (charging, a swap) restarts the filter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Measurement noise: battery readings (percent) and reported rates (%/s).
# A robot's own rate is a smoothed difference of noisy readings, good to
# about 0.02 %/s (just over 1 % per minute).
LEVEL_SIGMA = 0.5
RATE_SIGMA = 0.02
# Process noise per second: how fast the true level and rate wander. Drain
# follows load (idle, driving, climbing), so the rate has to be allowed to
# drift, or the filter lags a change in load and reports a range that misses
# the truth. How much depends on what is measured: when the robot reports its
# rate, the drift can be fast (about 0.01 %/s a minute), because the rate is
# observed directly; from levels alone it must be slow (about 0.0025), or the
# drain is lost in the noise of the readings.
LEVEL_Q = 0.0005
RATE_Q = 1e-7
RATE_Q_OBSERVED = 2e-6
# A rise larger than this means the battery is charging or was swapped.
JUMP = 2.0


@dataclass
class Runtime:
    minutes: float
    low: float
    high: float


class BatteryKalman:
    def __init__(self) -> None:
        self.t: float | None = None
        self.x = [0.0, 0.0]
        self.p = [[0.0, 0.0], [0.0, 0.0]]

    def _reset(self, t: float, level: float, rate: float | None) -> None:
        self.t = t
        self.x = [level, rate if rate is not None else 0.0]
        self.p = [[LEVEL_SIGMA**2, 0.0], [0.0, (RATE_SIGMA if rate is not None else 0.05) ** 2]]

    def update(self, t: float, level: float, rate: float | None = None) -> None:
        """Fold in a reading taken at ``t`` (epoch seconds)."""
        if self.t is None or level > self.x[0] + JUMP:
            self._reset(t, level, rate)
            return
        dt = t - self.t
        if dt <= 0:
            return  # out of order or repeated; the filter only moves forward
        self.t = t
        # Predict.
        b, r = self.x
        b += r * dt
        p00, p01, p11 = self.p[0][0], self.p[0][1], self.p[1][1]
        p00 = p00 + 2 * dt * p01 + dt * dt * p11 + LEVEL_Q * dt
        p01 = p01 + dt * p11
        p11 = p11 + (RATE_Q if rate is None else RATE_Q_OBSERVED) * dt
        # Update with the level, then with the rate if there is one.
        self.x, self.p = [b, r], [[p00, p01], [p01, p11]]
        self._measure(0, level, LEVEL_SIGMA**2)
        if rate is not None:
            self._measure(1, rate, RATE_SIGMA**2)

    def _measure(self, i: int, z: float, noise: float) -> None:
        """Scalar update of state component ``i``."""
        p = self.p
        s = p[i][i] + noise
        k = [p[0][i] / s, p[1][i] / s]
        y = z - self.x[i]
        self.x = [self.x[0] + k[0] * y, self.x[1] + k[1] * y]
        row = list(p[i])
        self.p = [
            [p[0][0] - k[0] * row[0], p[0][1] - k[0] * row[1]],
            [p[1][0] - k[1] * row[0], p[1][1] - k[1] * row[1]],
        ]

    @property
    def level(self) -> float:
        return self.x[0]

    @property
    def rate(self) -> float:
        return self.x[1]

    @property
    def rate_sigma(self) -> float:
        """One standard deviation of the rate estimate, %/s."""
        return math.sqrt(max(self.p[1][1], 0.0))

    def runtime(self) -> Runtime | None:
        """Minutes until empty with a two-sigma range, or None if not draining."""
        b, r = self.x
        if self.t is None or r >= -1e-6:
            return None
        minutes = -b / r / 60
        d_b, d_r = -1 / r, b / (r * r)
        var = d_b * d_b * self.p[0][0] + d_r * d_r * self.p[1][1] + 2 * d_b * d_r * self.p[0][1]
        spread = 2 * math.sqrt(max(var, 0.0)) / 60
        return Runtime(
            round(minutes, 1), round(max(0.0, minutes - spread), 1), round(minutes + spread, 1)
        )
