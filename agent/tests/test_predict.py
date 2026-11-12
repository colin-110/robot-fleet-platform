import json
import math
import random
from pathlib import Path

import pytest
from fleetops_agent import predict
from fleetops_agent.agent import Agent

VECTORS = json.loads(Path(__file__).with_name("predict_vectors.json").read_text())


@pytest.mark.parametrize("case", VECTORS["predictions"], ids=lambda c: str(c["t"]))
def test_predictions_match_the_shared_vectors(case):
    got = predict.predict(case["keyframe"], case["t"])
    assert got.keys() == case["expected"].keys()
    for key, value in case["expected"].items():
        assert got[key] == pytest.approx(value) if isinstance(value, float) else got[key] == value


@pytest.mark.parametrize("case", VECTORS["reasons"], ids=lambda c: c["name"])
def test_keyframe_reasons_match_the_shared_vectors(case):
    assert (
        predict.keyframe_reason(case["keyframe"], case["reading"], VECTORS["tolerances"])
        == (case["expected"])
    )


def test_the_vectors_cover_both_answers():
    answers = {c["expected"] for c in VECTORS["reasons"]}
    assert None in answers and {"position", "battery", "temperature", "status"} <= answers


def test_tolerances_come_from_config():
    tol = predict.tolerances({"tol_position_m": 2.0, "report_interval_s": 1.0})
    assert tol["tol_position_m"] == 2.0
    assert tol["heartbeat_s"] == predict.DEFAULTS["heartbeat_s"]
    assert "report_interval_s" not in tol


def test_battery_prediction_stays_in_range():
    kf = {"timestamp": 0.0, "battery": 1.0, "battery_rate": -1.0}
    assert predict.predict(kf, 10.0)["battery"] == 0.0


def test_rate_estimator_smooths_and_restarts_on_charging():
    est = predict.RateEstimator(position_alpha=0.5, battery_alpha=0.5)
    est.update({"timestamp": 0.0, "x": 0.0, "battery": 50.0})
    r = est.update({"timestamp": 1.0, "x": 1.0, "battery": 49.9})
    assert r["vx"] == pytest.approx(1.0) and r["battery_rate"] == pytest.approx(-0.1)
    r = est.update({"timestamp": 2.0, "x": 3.0, "battery": 49.8})
    assert r["vx"] == pytest.approx(1.5)
    r = est.update({"timestamp": 3.0, "x": 3.0, "battery": 52.0})
    assert r["battery_rate"] == pytest.approx(2.2)
    # A rate the robot reports itself wins.
    assert est.update({"timestamp": 4.0, "x": 4.0, "vx": 9.0})["vx"] == 9.0


def patrol(seconds: int, seed: int = 1):
    """A robot driving between waypoints at about 1 m/s, one reading a second."""
    rng = random.Random(seed)
    x = y = 0.0
    target = (10.0, 0.0)
    battery, temperature = 90.0, 35.0
    for t in range(seconds):
        dx, dy = target[0] - x, target[1] - y
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            target = (rng.uniform(-20, 20), rng.uniform(-20, 20))
        else:
            x += dx / dist
            y += dy / dist
        battery -= 0.02
        temperature += rng.uniform(-0.05, 0.05)
        yield {
            "timestamp": 1_790_000_000.0 + t,
            "x": x,
            "y": y,
            "battery": battery + rng.uniform(-0.1, 0.1),
            "temperature": temperature,
            "speed": 1.0,
            "status": "ACTIVE",
        }


def test_the_server_stays_within_tolerance_with_far_fewer_messages(tmp_path):
    agent = Agent(1, "key", "localhost", outbox_path=str(tmp_path / "o.db"))
    tol = predict.tolerances()
    worst = 0.0
    readings = list(patrol(600))
    for reading in readings:
        agent.observe(reading)
        # What the server shows at this instant: the last keyframe, carried forward.
        shown = predict.predict(agent._keyframe, reading["timestamp"])
        worst = max(worst, math.hypot(shown["x"] - reading["x"], shown["y"] - reading["y"]))
        assert abs(shown["battery"] - reading["battery"]) <= tol["tol_battery_pct"] + 0.01
    assert worst <= tol["tol_position_m"] + 0.01
    sent = agent.stats["keyframes"]
    assert sent == len(agent.outbox)
    # Straight legs need no messages; turns and the heartbeat do.
    assert sent < len(readings) / 5
    assert agent.stats["keyframe:heartbeat"] >= 1
    agent.outbox.close()


def test_a_status_change_is_sent_at_once(tmp_path):
    agent = Agent(1, "key", "localhost", outbox_path=str(tmp_path / "o.db"))
    base = {"timestamp": 100.0, "x": 0.0, "y": 0.0, "battery": 50.0, "status": "ACTIVE"}
    assert agent.observe(base) is True
    assert agent.observe({**base, "timestamp": 101.0}) is False
    assert agent.observe({**base, "timestamp": 102.0, "status": "CHARGING"}) is True
    assert agent.stats["keyframe:status"] == 1
    agent.outbox.close()


def test_tighter_tolerances_from_the_server_mean_more_keyframes(tmp_path):
    counts = []
    for tol in (2.0, 0.1):
        agent = Agent(1, "key", "localhost", outbox_path=str(tmp_path / f"{tol}.db"))
        agent._config = {"tol_position_m": tol}
        for reading in patrol(300):
            agent.observe(reading)
        counts.append(agent.stats["keyframes"])
        agent.outbox.close()
    assert counts[0] < counts[1]


def test_readings_milliseconds_apart_do_not_make_an_absurd_rate():
    from fleetops_agent.predict import RateEstimator

    est = RateEstimator()
    est.update({"timestamp": 0.0, "battery": 66.0})
    est.update({"timestamp": 2.0, "battery": 65.93})  # -0.035 %/s
    burst = est.update({"timestamp": 2.005, "battery": 65.87})  # 5 ms later
    assert abs(burst["battery_rate"]) < 0.1
    after = est.update({"timestamp": 4.0, "battery": 65.86})
    # Measured from the reading 2 s before, not from the one 5 ms before.
    assert after["battery_rate"] == pytest.approx(-0.035, abs=0.01)
