"""Offline suspicion by phi accrual (Hayashibara et al., 2004).

Instead of one fixed timeout, each robot's own gaps between messages are
learned (the last ``WINDOW`` of them), and the current silence is scored by
how unlikely it is under that history::

    phi = -log10(P(a gap at least this long))

phi 1 means a gap this long happens one time in ten; phi 8, one in a hundred
million. A robot that reports every second is suspect after a few seconds of
silence, and one on a 30-second heartbeat only after much longer, without
configuring either. The broker's last will remains the fast, certain signal;
this catches a robot that is connected but has stopped sending.
"""

from __future__ import annotations

import math
from collections import deque

WINDOW = 100
MIN_SAMPLES = 5
# A floor on the spread, so a perfectly regular robot is not declared dead
# the instant it is a little late.
MIN_STD_SECONDS = 1.0


class PhiAccrual:
    def __init__(self) -> None:
        self.last: float | None = None
        self.gaps: deque[float] = deque(maxlen=WINDOW)

    def heartbeat(self, now: float) -> None:
        if self.last is not None and now > self.last:
            self.gaps.append(now - self.last)
        self.last = now

    def phi(self, now: float) -> float | None:
        """Suspicion at ``now``; None until enough gaps have been seen."""
        if self.last is None or len(self.gaps) < MIN_SAMPLES:
            return None
        mean = sum(self.gaps) / len(self.gaps)
        var = sum((g - mean) ** 2 for g in self.gaps) / len(self.gaps)
        std = max(math.sqrt(var), MIN_STD_SECONDS, 0.1 * mean)
        z = (now - self.last - mean) / std
        later = 0.5 * math.erfc(z / math.sqrt(2))
        return -math.log10(max(later, 1e-300))
