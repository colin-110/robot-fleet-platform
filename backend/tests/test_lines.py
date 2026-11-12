"""Per-field line segments: always within tolerance of the stored path."""

import random

import pytest

from app.storage.lines import FIELDS, Line, fit, lines_for, path, worst_error

HEARTBEAT = 30.0


def kf(t, **values):
    return {"timestamp": float(t), **values}


def test_a_steady_drain_is_one_line():
    frames = [kf(t, battery=90 - 0.01 * t, battery_rate=-0.01) for t in range(0, 3600, 30)]
    lines = lines_for(frames, "battery", 3600, HEARTBEAT, 1.0)
    assert len(lines) == 1
    assert lines[0].value(1800) == pytest.approx(72.0, abs=1.0)


def test_a_value_that_holds_still_is_a_flat_line():
    frames = [kf(t, motor_health=80.0) for t in range(0, 3600, 30)]
    (line,) = lines_for(frames, "motor_health", 3600, HEARTBEAT, 2.0)
    assert line.slope == 0.0 and line.v0 == 80.0


def test_a_jump_bigger_than_the_tolerance_starts_a_new_line():
    frames = [kf(t, temperature=30.0) for t in range(0, 300, 30)]
    frames += [kf(t, temperature=60.0) for t in range(300, 600, 30)]
    lines = lines_for(frames, "temperature", 600, HEARTBEAT, 1.0)
    assert len(lines) == 2
    assert lines[1].t0 == 300 and lines[1].v0 == 60.0


def test_silence_past_the_heartbeat_is_not_covered():
    frames = [kf(0, speed=1.0), kf(30, speed=1.0), kf(300, speed=1.0), kf(330, speed=1.0)]
    lines = lines_for(frames, "speed", 360, HEARTBEAT, 0.3)
    covered = lambda t: any(ln.t0 <= t <= ln.t1 for ln in lines)  # noqa: E731
    assert covered(15) and covered(315)
    assert not covered(150)


def test_the_battery_clamp_at_zero_is_followed():
    frames = [kf(0, battery=2.0, battery_rate=-0.1)]
    runs = path(frames, "battery", 60, 60.0)
    lines = lines_for(frames, "battery", 60, 60.0, 0.5)
    assert worst_error(lines, runs) <= 0.5 + 1e-9
    # Flat at 0 after the battery runs out, 20 s in.
    assert min(ln.value(50) for ln in lines if ln.t0 <= 50 <= ln.t1) == pytest.approx(0, abs=0.5)


def test_each_line_starts_from_a_real_value():
    """Re-anchoring: error cannot carry from one segment into the next."""
    rng = random.Random(3)
    points, v = [], 0.0
    for t in range(0, 2000, 5):
        v += rng.uniform(-1, 1)
        points.append((float(t), v))
    real = dict(points)
    for line in fit(points, 0.5):
        assert line.v0 == real[line.t0]


@pytest.mark.parametrize("seed", range(200))
def test_every_field_stays_within_tolerance_everywhere(seed):
    """Random keyframe streams with rates, jumps, gaps and battery clamps."""
    rng = random.Random(seed)
    tolerance = {"x": 0.5, "y": 0.5, "battery": 1.0}
    frames, t = [], 0.0
    battery = rng.uniform(0, 100)
    for _ in range(rng.randint(1, 120)):
        t += rng.choice([0.5, 2, 5, 15, 29, 45, 200])  # some gaps exceed the heartbeat
        battery = min(100, max(0, battery + rng.uniform(-3, 3)))
        frames.append(
            kf(
                t,
                x=rng.uniform(-50, 50),
                vx=rng.uniform(-2, 2),
                y=rng.uniform(-50, 50),
                vy=rng.uniform(-2, 2),
                battery=battery,
                battery_rate=rng.uniform(-0.5, 0.5),
                temperature=rng.choice([30.0, 31.0, 45.0]),
                speed=rng.uniform(0, 2),
                mission_progress=rng.uniform(0, 100),
                battery_health=80.0,
                motor_health=rng.choice([70.0, 71.5]),
                sensor_health=None if rng.random() < 0.1 else 90.0,
                network_health=95.0,
            )
        )
    end = t + rng.uniform(0, 60)
    for name in FIELDS:
        tol = tolerance.get(name, 1.0)
        runs = path(frames, name, end, HEARTBEAT)
        lines = lines_for(frames, name, end, HEARTBEAT, tol)
        assert worst_error(lines, runs) <= tol + 1e-9, name


def test_fewer_lines_than_points_on_a_smooth_path():
    points = [(float(t), 0.001 * t * t) for t in range(0, 600)]
    lines = fit(points, 1.0)
    assert len(lines) < len(points) / 20
    assert worst_error(lines, [points]) <= 1.0 + 1e-9


def test_a_line_reports_its_value():
    assert Line(10, 20, 5.0, 0.5).value(14) == 7.0


def test_a_heartbeat_a_tick_late_is_not_a_gap():
    """The robot sends its heartbeat on the first tick after 30 s."""
    frames = [kf(t, battery_health=90.0) for t in (0, 31.5, 63.0, 94.2)]
    assert len(lines_for(frames, "battery_health", 120, HEARTBEAT, 2.0)) == 1


def test_a_window_is_clipped_with_the_value_at_its_start():
    frames = [kf(0, battery=50.0, battery_rate=-0.1), kf(30, battery=47.0, battery_rate=-0.1)]
    (first, *_) = lines_for(frames, "battery", 60, HEARTBEAT, 0.5, start=10)
    assert first.t0 == 10 and first.v0 == pytest.approx(49.0)


@pytest.mark.parametrize("seed", range(50))
def test_windows_stay_within_tolerance(seed):
    rng = random.Random(1000 + seed)
    frames, t = [], 0.0
    for _ in range(80):
        t += rng.choice([1, 5, 20, 40, 120])
        frames.append(kf(t, x=rng.uniform(-9, 9), vx=rng.uniform(-1, 1)))
    start, end = rng.uniform(0, t / 2), t + 10
    runs = path(frames, "x", end, HEARTBEAT, start)
    lines = lines_for(frames, "x", end, HEARTBEAT, 0.5, start)
    assert all(ln.t0 >= start - 1e-9 for ln in lines)
    assert worst_error(lines, runs) <= 0.5 + 1e-9


def test_a_state_is_stored_as_its_changes():
    from app.storage.lines import changes

    frames = [kf(t, status="ACTIVE") for t in range(0, 120, 20)]
    frames += [kf(t, status="CHARGING") for t in range(120, 300, 20)]
    assert changes(frames, "status", 300, HEARTBEAT, 0) == [(0.0, "ACTIVE"), (120.0, "CHARGING")]


def test_a_state_window_starts_with_the_value_in_force():
    from app.storage.lines import changes

    frames = [kf(0, mission_id="M-1"), kf(20, mission_id="M-1"), kf(40, mission_id="M-2")]
    assert changes(frames, "mission_id", 60, HEARTBEAT, 30) == [(30.0, "M-1"), (40.0, "M-2")]


def test_silence_makes_a_state_unknown():
    from app.storage.lines import changes

    frames = [kf(0, status="ACTIVE"), kf(300, status="ACTIVE")]
    assert changes(frames, "status", 330, HEARTBEAT, 0) == [
        (0.0, "ACTIVE"),
        (45.0, None),  # heartbeat 30 s with its 1.5x grace
        (300.0, "ACTIVE"),
    ]
