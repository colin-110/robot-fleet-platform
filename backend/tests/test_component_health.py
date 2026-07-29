"""Component health must survive the trip from telemetry to the API.

Regression tests for a truthiness check that rendered a completely failed
component (0.0) as perfect health (100.0) — inverting the most urgent reading
the system can receive.
"""

from datetime import datetime, timezone

import pytest

from app.services.robot_service import _health, summarize_robot_history


class Row:
    """A telemetry row with only the fields the summariser reads."""

    def __init__(self, **overrides):
        now = datetime.now(timezone.utc)
        defaults = {
            "robot_id": 1,
            "battery": 80.0,
            "temperature": 30.0,
            "speed": 1.0,
            "status": "ACTIVE",
            "mission_id": None,
            "mission_type": None,
            "mission_progress": None,
            "mission_start_time": None,
            "timestamp": now,
            "x": 1.0,
            "y": 2.0,
            "battery_health": 90.0,
            "motor_health": 90.0,
            "sensor_health": 90.0,
            "network_health": 90.0,
        }
        for key, value in {**defaults, **overrides}.items():
            setattr(self, key, value)


# ── The helper ──────────────────────────────────────────────────────


def test_zero_is_reported_as_zero():
    """The bug: `round(v, 2) if v else 100.0` turns 0.0 into 100.0."""
    assert _health(0.0) == 0.0


def test_missing_reading_defaults_to_full_health():
    """Devices predating per-component reporting must not read as dead."""
    assert _health(None) == 100.0


@pytest.mark.parametrize("value", [0.0, 0.01, 1.0, 12.5, 99.99, 100.0])
def test_reported_values_pass_through_rounded(value):
    assert _health(value) == round(value, 2)


# ── Through the summariser ──────────────────────────────────────────


def test_a_dead_component_is_not_rendered_as_healthy():
    """A robot reporting total component failure must surface as failed.

    This is the failure mode the whole roster design exists to prevent: not
    "shows nothing" but "confidently shows the opposite".
    """
    summary = summarize_robot_history(
        [
            Row(
                battery_health=0.0,
                motor_health=0.0,
                sensor_health=0.0,
                network_health=0.0,
            )
        ]
    )

    assert summary["battery_health"] == 0.0
    assert summary["motor_health"] == 0.0
    assert summary["sensor_health"] == 0.0
    assert summary["network_health"] == 0.0


def test_one_failed_component_among_healthy_ones_survives():
    summary = summarize_robot_history([Row(motor_health=0.0)])

    assert summary["motor_health"] == 0.0
    assert summary["battery_health"] == 90.0


def test_absent_component_readings_default_to_full_health():
    summary = summarize_robot_history(
        [
            Row(
                battery_health=None,
                motor_health=None,
                sensor_health=None,
                network_health=None,
            )
        ]
    )

    assert summary["battery_health"] == 100.0
    assert summary["motor_health"] == 100.0
