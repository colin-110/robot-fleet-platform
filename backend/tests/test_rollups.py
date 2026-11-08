"""Exact rollups from keyframe segments."""

import pytest

from app.mqtt import predict
from app.storage.rollups import minute_buckets, segments

T = 1_800_000_000.0  # on a minute boundary


def kf(t, **values):
    return {"timestamp": T + t, **values}


def test_a_line_averages_to_its_midpoint():
    # 80 % draining at 0.1 %/s for the whole minute: the average is the midpoint.
    frames = [kf(0, battery=80.0, battery_rate=-0.1, temperature=40.0)]
    [bucket] = minute_buckets(frames, T, T + 60, heartbeat_s=60).values()
    row = bucket.row(1)
    assert row["battery_avg"] == pytest.approx(77.0)
    assert (row["battery_min"], row["battery_max"]) == (pytest.approx(74.0), pytest.approx(80.0))
    assert row["seconds_covered"] == 60
    assert row["temperature_avg"] == 40.0


def test_time_weighted_not_row_weighted():
    # 50 % for 50 s, then nine quick keyframes at 90 % in the last 10 s.
    # AVG() over rows says 86 %; the time-weighted truth is about 58 %.
    frames = [kf(0, battery=50.0)] + [kf(50 + i, battery=90.0) for i in range(10)]
    row = minute_buckets(frames, T, T + 60, heartbeat_s=60)[T].row(1)
    assert sum(f["battery"] for f in frames) / len(frames) == pytest.approx(86.36, abs=0.01)
    assert row["battery_avg"] == pytest.approx((50 * 50 + 90 * 10) / 60)
    assert row["keyframes"] == 11


def test_a_segment_crossing_buckets_is_split():
    frames = [kf(30, battery=60.0, battery_rate=-0.1, vx=1.0, vy=0.0)]
    buckets = minute_buckets(frames, T, T + 120, heartbeat_s=60)
    first, second = buckets[T].row(1), buckets[T + 60].row(1)
    assert first["seconds_covered"] == 30 and second["seconds_covered"] == 30
    assert first["battery_avg"] == pytest.approx(58.5)
    assert second["battery_avg"] == pytest.approx(55.5)
    assert first["distance_m"] + second["distance_m"] == pytest.approx(60.0)


def test_silence_past_the_heartbeat_is_not_covered():
    frames = [kf(0, battery=50.0)]
    row = minute_buckets(frames, T, T + 60, heartbeat_s=20)[T].row(1)
    assert row["seconds_covered"] == 20
    assert row["battery_avg"] == 50.0


def test_the_battery_clamps_at_empty():
    # Hits 0 % after 10 s and stays there for the remaining 50.
    frames = [kf(0, battery=1.0, battery_rate=-0.1)]
    row = minute_buckets(frames, T, T + 60, heartbeat_s=60)[T].row(1)
    assert row["battery_avg"] == pytest.approx((0.5 * 10) / 60)
    assert row["battery_min"] == 0.0


def test_the_rollup_matches_the_predictor_everywhere():
    # Integrate the shared predictor numerically and compare.
    frames = [
        kf(0, battery=90.0, battery_rate=-0.05, temperature=40.0),
        kf(25, battery=89.0, battery_rate=-0.2, temperature=42.0),
        kf(70, battery=80.0, battery_rate=0.3, temperature=41.0),
    ]
    buckets = minute_buckets(frames, T, T + 120, heartbeat_s=30)
    for start, bucket in buckets.items():
        samples = []
        for i in range(6000):
            t = start + (i + 0.5) * 0.01
            live = [f for f in frames if f["timestamp"] <= t][-1]
            if t - live["timestamp"] < 30:
                samples.append(predict.predict(live, t)["battery"])
        assert bucket.row(1)["battery_avg"] == pytest.approx(sum(samples) / len(samples), abs=1e-3)
        assert bucket.row(1)["seconds_covered"] == pytest.approx(len(samples) * 0.01, abs=0.02)


def test_segments_end_at_the_next_keyframe():
    segs = segments([kf(0, battery=1.0), kf(10, battery=2.0)], T + 100, heartbeat_s=30)
    assert [(s.t0 - T, s.t1 - T) for s in segs] == [(0, 10), (10, 40)]
