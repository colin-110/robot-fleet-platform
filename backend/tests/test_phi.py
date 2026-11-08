from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.intelligence import fleet
from app.intelligence.phi import PhiAccrual


def regular(interval: float, n: int = 20) -> PhiAccrual:
    detector = PhiAccrual()
    for i in range(n):
        detector.heartbeat(i * interval)
    return detector


def test_nothing_is_said_until_gaps_are_learned():
    detector = PhiAccrual()
    detector.heartbeat(0.0)
    detector.heartbeat(1.0)
    assert detector.phi(100.0) is None


def test_suspicion_grows_with_silence():
    detector = regular(1.0)
    last = 19.0
    values = [detector.phi(last + s) for s in (0.5, 2.0, 5.0, 10.0)]
    assert values == sorted(values)
    assert values[0] < 1 and values[-1] > 8


def test_each_robot_is_judged_by_its_own_rhythm():
    fast, slow = regular(1.0), regular(30.0)
    # Ten seconds of silence is alarming for the first, normal for the second.
    assert fast.phi(19.0 + 10) > 8
    assert slow.phi(19 * 30.0 + 10) < 1


def test_the_fleet_model_times_arrivals_per_batch():
    row = SimpleNamespace(
        robot_id=7,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        battery=50.0,
        temperature=30.0,
        battery_health=None,
        motor_health=None,
        sensor_health=None,
        network_health=None,
        battery_rate=None,
    )
    for t in range(10):
        fleet.observe([row, row], now=float(t))
    assert fleet.suspicion(7, now=9.5) == pytest.approx(fleet.get(7).arrivals.phi(9.5))
    assert len(fleet.get(7).arrivals.gaps) == 9
