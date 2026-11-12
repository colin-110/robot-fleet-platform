"""The battery's Kalman filter and the runtime it gives fleet status."""

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.intelligence import fleet
from app.intelligence.kalman import BatteryKalman
from app.schemas import TelemetryCreate
from app.services.robot_service import summarize_state
from app.services.telemetry_service import store_readings
from tests.conftest import TestSessionLocal
from tests.test_robot_service import state


def test_the_drain_is_found_through_noise():
    rng = random.Random(3)
    kf = BatteryKalman()
    true_rate = -0.01  # % per second, 0.6 % per minute
    for t in range(0, 1200, 5):
        kf.update(float(t), 90.0 + true_rate * t + rng.gauss(0, 0.5))
    assert kf.rate == pytest.approx(true_rate, rel=0.1)
    runtime = kf.runtime()
    expected = (90.0 + true_rate * 1195) / -true_rate / 60
    assert runtime.low <= expected <= runtime.high
    assert runtime.minutes == pytest.approx(expected, rel=0.1)


def test_the_range_narrows_with_more_readings():
    kf = BatteryKalman()
    spans = []
    for t in range(0, 600, 10):
        kf.update(float(t), 80.0 - 0.005 * t)
        if t in (60, 590):
            r = kf.runtime()
            spans.append(r.high - r.low)
    assert spans[1] < spans[0] / 2


def test_a_reported_rate_is_used():
    kf = BatteryKalman()
    kf.update(0.0, 50.0, rate=-0.02)
    kf.update(10.0, 49.8, rate=-0.02)
    assert kf.rate == pytest.approx(-0.02, rel=0.05)


def test_charging_restarts_and_has_no_runtime():
    kf = BatteryKalman()
    for t in range(0, 100, 10):
        kf.update(float(t), 50.0 - 0.01 * t)
    kf.update(110.0, 60.0)
    assert kf.level == 60.0
    assert kf.runtime() is None
    for t in range(120, 200, 10):
        kf.update(float(t), 60.0 + 0.05 * (t - 110))
    assert kf.rate > 0 and kf.runtime() is None


def test_out_of_order_readings_are_ignored():
    kf = BatteryKalman()
    kf.update(10.0, 50.0)
    kf.update(5.0, 10.0)
    assert kf.level == 50.0


async def test_fleet_status_uses_the_filter():
    now = datetime.now(timezone.utc)
    readings = [
        TelemetryCreate(
            robot_id=31,
            battery=60.0 - 0.6 * i,
            temperature=30.0,
            speed=1.0,
            timestamp=now - timedelta(minutes=10 - i),
        )
        for i in range(10)
    ]
    async with TestSessionLocal() as db:
        await store_readings(db, readings)
    summary = summarize_state(
        state(robot_id=31, battery=54.6, timestamp=now - timedelta(minutes=1))
    )
    low, high = summary["runtime_range_minutes"]
    # 0.6 % per minute from about 54 %: about 90 minutes.
    assert low <= summary["runtime_remaining_minutes"] <= high
    assert 80 < summary["runtime_remaining_minutes"] < 100
    assert fleet.get(31).battery.rate == pytest.approx(-0.01, rel=0.05)


def test_a_drain_that_speeds_up_is_followed_and_the_range_admits_it():
    # Shaped like a simulated robot starting a mission: the drain climbs from
    # 0.03 to 0.4 % per second over a minute, then holds.
    kf = BatteryKalman()
    level, t = 97.0, 0.0
    while t < 150:
        rate = -min(0.4, 0.03 + 0.37 * t / 60)
        kf.update(t, level, rate)
        level += rate * 4
        t += 4
    true_minutes = level / 0.4 / 60
    runtime = kf.runtime()
    assert runtime.low <= true_minutes <= runtime.high


def test_an_impossible_reported_rate_is_ignored():
    from app.intelligence.kalman import BatteryKalman

    f = BatteryKalman()
    for t in range(0, 300, 10):
        f.update(float(t), 80.0 - 0.01 * t, -0.01)
    f.update(305.0, 76.95, -12.0)  # 720 %/min: two readings milliseconds apart
    assert f.rate == pytest.approx(-0.01, abs=0.005)
