"""Dual prediction on the server: carrying keyframes forward and rebuilding tracks."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Telemetry
from app.mqtt.ingest import IngestConsumer
from app.schemas import TelemetryCreate
from app.services.robot_service import summarize_state
from app.services.telemetry_service import store_readings
from tests.conftest import TestSessionLocal
from tests.test_mqtt_ingest import FakeClient, env_delivery
from tests.test_robot_service import state

NOW = datetime.now(timezone.utc)


def test_a_keyframe_is_carried_forward_to_now():
    NOW = datetime.now(timezone.utc)
    summary = summarize_state(
        state(
            timestamp=NOW - timedelta(seconds=10), x=10.0, y=0.0, vx=1.0, vy=-0.5, battery_rate=-0.1
        )
    )
    assert summary["predicted"] is True
    assert summary["x"] == pytest.approx(20.0, abs=0.2)
    assert summary["y"] == pytest.approx(-5.0, abs=0.1)
    assert summary["battery"] == pytest.approx(99.0, abs=0.05)
    assert summary["bounds"]["position_m"] == 0.5
    assert (summary["keyframe_x"], summary["keyframe_battery"]) == (10.0, 100.0)
    # The robot's own drain rate (6 %/min) gives the runtime.
    assert summary["runtime_remaining_minutes"] == pytest.approx(99.0 / 6, abs=0.2)


def test_prediction_stops_at_the_heartbeat():
    summary = summarize_state(
        state(
            robot_id=902,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=100),
            x=0.0,
            vx=1.0,
            vy=0.0,
        ),
        {"heartbeat_s": 60.0},
    )
    # Not offline yet (twice the heartbeat), but no longer extrapolated past it.
    assert summary["status"] == "ACTIVE"
    assert summary["x"] == pytest.approx(60.0)


def test_the_robots_tolerances_set_the_bounds():
    summary = summarize_state(
        state(robot_id=903, vx=0.0), {"tol_position_m": 2.0, "tol_battery_pct": 0.5}
    )
    assert summary["bounds"]["position_m"] == 2.0
    assert summary["bounds"]["battery_pct"] == 0.5


def test_a_robot_without_rates_is_shown_as_reported():
    summary = summarize_state(state(robot_id=904, timestamp=NOW - timedelta(seconds=10), x=10.0))
    assert summary["predicted"] is False
    assert summary["bounds"] is None
    assert summary["x"] == 10.0


def test_an_offline_robot_is_not_extrapolated():
    summary = summarize_state(
        state(robot_id=905, timestamp=NOW - timedelta(minutes=5), x=0.0, vx=1.0)
    )
    assert summary["status"] == "OFFLINE"
    assert summary["x"] == 0.0


def keyframe(seconds, **values):
    base = {"robot_id": 9, "battery": 80.0, "temperature": 30.0, "speed": 1.0}
    return TelemetryCreate(**base, timestamp=NOW - timedelta(seconds=seconds), **values)


async def test_the_track_is_rebuilt_from_keyframes(client):
    async with TestSessionLocal() as db:
        await store_readings(
            db,
            [
                keyframe(100, x=0.0, y=0.0, vx=1.0, vy=0.0, battery_rate=-0.01),
                keyframe(90, x=10.0, y=0.0, vx=0.0, vy=2.0, battery_rate=-0.01),
            ],
        )
    start = (NOW - timedelta(seconds=105)).isoformat()
    end = (NOW - timedelta(seconds=80)).isoformat()
    response = await client.get(
        "/api/v1/robots/9/track", params={"start": start, "end": end, "step_s": 5}
    )
    assert response.status_code == 200
    points = response.json()
    # Nothing before the first keyframe; then the first carried forward, then the second.
    assert len(points) == 5
    assert [p["keyframe"] for p in points] == [True, False, True, False, False]
    assert [round(p["x"], 1) for p in points] == [0.0, 5.0, 10.0, 10.0, 10.0]
    assert [round(p["y"], 1) for p in points] == [0.0, 0.0, 0.0, 10.0, 20.0]


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z", "step_s": 1},
    ],
)
async def test_unreasonable_track_requests_are_refused(client, params):
    response = await client.get("/api/v1/robots/9/track", params=params)
    assert response.status_code == 422


async def test_rates_arrive_through_ingest_and_reach_fleet_status(client):
    NOW = datetime.now(timezone.utc)
    consumer = IngestConsumer(session_factory=TestSessionLocal)
    consumer.client = FakeClient()
    consumer.epoch = 1
    reading = {
        "timestamp": NOW - timedelta(seconds=4),
        "battery": 70.0,
        "temperature": 30.0,
        "speed": 1.0,
        "x": 1.0,
        "y": 1.0,
        "vx": 0.5,
        "vy": 0.0,
        "battery_rate": -0.002,
    }
    await consumer.process([env_delivery(4, "telemetry", [reading], 1)])
    async with TestSessionLocal() as db:
        row = (await db.execute(select(Telemetry).where(Telemetry.robot_id == 4))).scalar_one()
    assert (row.vx, row.vy, row.battery_rate) == (0.5, 0.0, -0.002)

    fleet = (await client.get("/api/v1/robots/status")).json()
    robot = next(r for r in fleet if r["robot_id"] == 4)
    assert robot["predicted"] is True
    assert robot["x"] == pytest.approx(3.0, abs=0.2)
