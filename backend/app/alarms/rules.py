"""Alarm rules and the hysteresis that keeps them from flapping.

Each rule watches one metric. It raises once the metric has been past
``raise_at`` for ``hold_s`` seconds, and clears only once it is back past
``clear_at``, a separate threshold on the safe side. A battery hovering
around 20 % therefore raises one alarm, not one per reading, and a robot
that reconnects within the hold time raises none.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    kind: str
    severity: str  # "warning" or "critical"
    metric: str
    above: bool  # True: raised by high values; False: by low ones
    raise_at: float
    clear_at: float
    hold_s: float
    message: str  # formatted with value=, and feature= for anomalies

    def breached(self, value: float) -> bool:
        return value > self.raise_at if self.above else value < self.raise_at

    def recovered(self, value: float) -> bool:
        return value < self.clear_at if self.above else value > self.clear_at


RULES = (
    Rule("BATTERY_CRITICAL", "critical", "battery", False, 10, 15, 0, "Battery at {value:.0f}%"),
    Rule("BATTERY_LOW", "warning", "battery", False, 20, 25, 10, "Battery at {value:.0f}%"),
    Rule("OVERHEATING", "critical", "temperature", True, 75, 70, 10, "Temperature {value:.0f} C"),
    Rule(
        "COMPONENT_FAILING",
        "warning",
        "health",
        False,
        30,
        35,
        30,
        "A component is down to {value:.0f}% health",
    ),
    Rule(
        "ANOMALY",
        "warning",
        "anomaly",
        True,
        4.0,
        3.0,
        60,
        "Unusual {feature} compared with the fleet (z {value:.1f})",
    ),
    Rule("SILENT", "warning", "suspicion", True, 8.0, 3.0, 10, "Connected but not sending"),
    Rule("OFFLINE", "critical", "offline", True, 0.5, 0.5, 30, "Disconnected from the broker"),
    Rule(
        "DUPLICATE_SESSION",
        "critical",
        "connections_per_minute",
        True,
        4,
        1,
        0,
        "Connected {value:.0f} times in a minute: is it running twice?",
    ),
)


class Tracker:
    """Which conditions are pending (breached, not yet held long enough)."""

    def __init__(self) -> None:
        self.pending: dict[tuple[int, str], float] = {}

    def step(self, rule: Rule, robot_id: int, value: float | None, now: float, open_: bool):
        """``"raise"``, ``"clear"`` or None for this robot and rule at ``now``."""
        if value is None:
            return None
        key = (robot_id, rule.kind)
        if open_:
            self.pending.pop(key, None)
            return "clear" if rule.recovered(value) else None
        if not rule.breached(value):
            self.pending.pop(key, None)
            return None
        since = self.pending.setdefault(key, now)
        if now - since >= rule.hold_s:
            del self.pending[key]
            return "raise"
        return None
