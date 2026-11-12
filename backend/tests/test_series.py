"""GET /robots/{id}/series: stored segments for finished hours, live for the rest."""

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas import TelemetryCreate
from app.services.telemetry_service import store_readings
from app.storage import lines_job
from app.storage.lines_job import LinesJob
from tests.conftest import TestSessionLocal

HOUR = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)


def reading(seconds, battery, status="ACTIVE"):
    return TelemetryCreate(
        robot_id=1,
        status=status,
        battery=battery,
        battery_rate=-0.01,
        temperature=40.0,
        speed=1.0,
        timestamp=HOUR + timedelta(seconds=seconds),
    )


@pytest.fixture(autouse=True)
def fresh_state():
    lines_job._dirty.clear()
    lines_job._done_until = None
    yield
    lines_job._dirty.clear()
    lines_job._done_until = None


async def two_hours_with_the_first_done():
    async with TestSessionLocal() as db:
        await store_readings(
            db,
            [
                reading(t, 90.0 - 0.01 * t, status="ACTIVE" if t < 4000 else "CHARGING")
                for t in range(0, 7200, 20)
            ],
        )
    # Ten minutes into the second hour: the first is done, the second is not.
    await LinesJob(TestSessionLocal).run_once((HOUR + timedelta(hours=1, minutes=10)).timestamp())


def query(field, start, end):
    return {"field": field, "start": start.isoformat(), "end": end.isoformat()}


def value_at(segments, when):
    for s in segments:
        t0, t1 = datetime.fromisoformat(s["t0"]), datetime.fromisoformat(s["t1"])
        if t0 <= when <= t1:
            return s["v0"] + s["slope"] * (when - t0).total_seconds()
    return None


async def test_finished_hours_are_stored_and_the_rest_is_fitted_live(client):
    await two_hours_with_the_first_done()
    body = (
        await client.get(
            "/api/v1/robots/1/series", params=query("battery", HOUR, HOUR + timedelta(hours=2))
        )
    ).json()
    stored = [s for s in body["segments"] if s["stored"]]
    live = [s for s in body["segments"] if not s["stored"]]
    assert stored and live
    assert all(datetime.fromisoformat(s["t0"]) < HOUR + timedelta(hours=1) for s in stored)
    assert value_at(body["segments"], HOUR + timedelta(minutes=30)) == pytest.approx(72.0, abs=1.0)
    assert value_at(body["segments"], HOUR + timedelta(minutes=90)) == pytest.approx(36.0, abs=1.0)
    assert body["error_bound"] == {"robot": 1.0, "server": 1.0, "total": 2.0}


async def test_status_comes_back_as_its_changes(client):
    await two_hours_with_the_first_done()
    body = (
        await client.get(
            "/api/v1/robots/1/series",
            params=query("status", HOUR + timedelta(minutes=5), HOUR + timedelta(hours=2)),
        )
    ).json()
    values = [(c["value"], c["stored"]) for c in body["changes"]]
    # In force at the start (stored, from the hour's first row), then the
    # change in the unfinished hour (live).
    assert values[0] == ("ACTIVE", True)
    assert ("CHARGING", False) in values


@pytest.mark.parametrize(
    "params,detail",
    [
        (query("altitude", HOUR, HOUR + timedelta(hours=1)), "Unknown field"),
        (query("battery", HOUR, HOUR - timedelta(hours=1)), "end must be after start"),
        (query("battery", HOUR, HOUR + timedelta(days=8)), "At most 7 days"),
    ],
)
async def test_bad_requests_are_refused(client, params, detail):
    response = await client.get("/api/v1/robots/1/series", params=params)
    assert response.status_code == 422
    assert detail in response.json()["detail"]


async def test_a_finished_hour_with_nothing_stored_is_fitted_from_raw_keyframes(client):
    """An hour done before a field was stored still answers, from the raw rows."""
    from sqlalchemy import delete

    from app.models import TelemetryState

    await two_hours_with_the_first_done()
    async with TestSessionLocal() as db:
        await db.execute(delete(TelemetryState))
        await db.commit()
    body = (
        await client.get(
            "/api/v1/robots/1/series",
            params=query("status", HOUR + timedelta(minutes=5), HOUR + timedelta(hours=2)),
        )
    ).json()
    assert body["changes"][0]["value"] == "ACTIVE"
    assert [c["value"] for c in body["changes"]].count("CHARGING") == 1
