"""Writes predict_vectors.json: cases every copy of the predictor must agree on.

Run from agent/: python tests/make_predict_vectors.py
"""

import json
from pathlib import Path

from fleetops_agent import predict

KF = {
    "timestamp": 1000.0,
    "x": 1.0,
    "y": -2.0,
    "vx": 0.5,
    "vy": -0.25,
    "battery": 80.0,
    "battery_rate": -0.01,
    "temperature": 40.0,
    "speed": 0.56,
    "status": "ACTIVE",
    "mission_id": "M-1",
    "mission_progress": 10.0,
    "battery_health": 95.0,
}

TOL = predict.tolerances()

predictions = [
    {"keyframe": KF, "t": t, "expected": predict.predict(KF, t)}
    for t in (1000.0, 1004.0, 1030.5, 990.0)
]
predictions.append(
    {
        "keyframe": {**KF, "battery": 0.5, "battery_rate": -0.1},
        "t": 1010.0,
        "expected": predict.predict({**KF, "battery": 0.5, "battery_rate": -0.1}, 1010.0),
    }
)

reading = predict.predict(KF, 1004.0)
cases = [
    ("on the prediction", reading),
    ("position off by 0.4 m", {**reading, "x": reading["x"] + 0.4}),
    (
        "position off by 0.6 m",
        {**reading, "x": reading["x"] + 0.36, "y": reading["y"] + 0.48 + 0.01},
    ),
    ("battery off by 1.5", {**reading, "battery": reading["battery"] - 1.5}),
    ("temperature off by 0.9", {**reading, "temperature": 40.9}),
    ("temperature off by 1.1", {**reading, "temperature": 41.1}),
    ("status changed", {**reading, "status": "CHARGING"}),
    ("mission started", {**reading, "mission_id": "M-2"}),
    ("heartbeat due", {**reading, "timestamp": 1030.0}),
    ("a value appeared", {**reading, "motor_health": 90.0}),
]
reasons = [
    {"name": name, "keyframe": KF, "reading": r, "expected": predict.keyframe_reason(KF, r, TOL)}
    for name, r in cases
]
reasons.append(
    {"name": "no keyframe yet", "keyframe": None, "reading": reading, "expected": "first"}
)

out = {"tolerances": TOL, "predictions": predictions, "reasons": reasons}
Path(__file__).with_name("predict_vectors.json").write_text(json.dumps(out, indent=1) + "\n")
