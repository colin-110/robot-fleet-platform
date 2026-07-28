"""
Regression tests for fleet visibility and telemetry time fidelity.

Each test here corresponds to a bug that shipped:

- A robot silent longer than the telemetry window vanished from the fleet
  list entirely, because the list was derived from recent telemetry rather
  than from a roster.
- Batch ingestion stamped every reading in a batch with a single server
  timestamp, collapsing readings taken seconds apart onto one instant.
- Devices had no way to report when a reading was actually taken, which made
  store-and-forward impossible.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.models import Robot, Telemetry
from app.services.robot_service import summarize_silent_robot
from app.services.telemetry_service import resolve_timestamp


def _payload(robot_id: int, **overrides) -> dict:
    payload = {
        "robot_id": robot_id,
        "battery": 80.0,
        "temperature": 30.0,
        "speed": 1.0,
        "status": "ACTIVE",
        "battery_health": 95.0,
        "motor_health": 95.0,
        "sensor_health": 95.0,
        "network_health": 95.0,
        "x": 1.0,
        "y": 2.0,
    }
    payload.update(overrides)
    return payload


# ── Fleet visibility ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registered_robot_with_no_telemetry_still_appears(client: AsyncClient, db):
    """A robot on the roster is listed even with zero telemetry rows.

    This is the core regression. Previously the fleet list came from a query
    over recent telemetry, so a robot that stopped reporting produced no rows,
    dropped out of the result, and disappeared from the dashboard — the one
    failure a monitoring system must not have.
    """
    db.add(Robot(id=7, is_active=True, last_seen=None))
    await db.commit()

    response = await client.get("/api/v1/robots/status")
    assert response.status_code == 200

    fleet = {r["robot_id"]: r for r in response.json()}
    assert 7 in fleet, "registered robot vanished from the fleet list"
    assert fleet[7]["status"] == "OFFLINE"


@pytest.mark.asyncio
async def test_robot_silent_beyond_window_reports_last_seen(client: AsyncClient, db):
    """A long-silent robot keeps its last-seen time from the roster.

    Its telemetry is outside the query window, so ``last_seen`` cannot come
    from the telemetry table — the roster is the only remaining record of when
    the unit was last heard from.
    """
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    db.add(Robot(id=8, is_active=True, last_seen=long_ago))
    await db.commit()

    response = await client.get("/api/v1/robots/status")
    fleet = {r["robot_id"]: r for r in response.json()}

    assert fleet[8]["status"] == "OFFLINE"
    assert fleet[8]["last_seen"] is not None
    assert fleet[8]["last_seen"].startswith(long_ago.strftime("%Y-%m-%d"))


@pytest.mark.asyncio
async def test_decommissioned_robot_is_excluded(client: AsyncClient, db):
    """Retiring a robot removes it from the fleet without deleting history."""
    db.add(Robot(id=9, is_active=False, last_seen=datetime.now(timezone.utc)))
    await db.commit()

    response = await client.get("/api/v1/robots/status")
    assert 9 not in {r["robot_id"] for r in response.json()}


@pytest.mark.asyncio
async def test_unregistered_robot_reporting_telemetry_is_not_dropped(
    client: AsyncClient,
):
    """Telemetry arriving before the roster upsert still shows up.

    The worker registers robots as it persists batches, so there is a brief
    window where telemetry exists for a robot with no roster row. It must be
    listed rather than silently skipped.
    """
    await client.post("/api/v1/telemetry", json=_payload(11))

    response = await client.get("/api/v1/robots/status")
    fleet = {r["robot_id"]: r for r in response.json()}
    assert 11 in fleet
    assert fleet[11]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_stale_telemetry_marks_robot_offline(client: AsyncClient, db):
    """A robot inside the window but past the offline threshold reads OFFLINE."""
    settings = get_settings()
    stale = datetime.now(timezone.utc) - timedelta(seconds=settings.offline_after_seconds + 30)
    db.add(Robot(id=12, is_active=True, last_seen=stale))
    db.add(
        Telemetry(
            robot_id=12,
            battery=50.0,
            temperature=30.0,
            speed=0.0,
            status="ACTIVE",
            timestamp=stale,
        )
    )
    await db.commit()

    response = await client.get("/api/v1/robots/status")
    fleet = {r["robot_id"]: r for r in response.json()}
    assert fleet[12]["status"] == "OFFLINE", "stale robot should not report ACTIVE"


def test_silent_robot_summary_does_not_fabricate_readings():
    """A silent robot's telemetry fields are zeroed, not stale-but-plausible.

    Showing a battery percentage from an unknown time next to an OFFLINE badge
    invites an operator to trust a number that could be hours old.
    """
    seen = datetime.now(timezone.utc) - timedelta(hours=5)
    summary = summarize_silent_robot(Robot(id=3, is_active=True, last_seen=seen))

    assert summary["status"] == "OFFLINE"
    assert summary["battery"] == 0.0
    assert summary["runtime_remaining_minutes"] is None
    assert summary["mission_id"] is None
    assert summary["last_seen"] is not None


# ── Timestamp fidelity ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_preserves_distinct_timestamps(client: AsyncClient, db):
    """Readings in one batch keep their own times.

    Previously the whole batch was stamped once with server time, so fifty
    readings taken seconds apart all landed on the same instant. That flattens
    the per-minute trend buckets and zeroes the time span the battery
    drain-rate extrapolation divides by.
    """
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    batch = [
        _payload(20, battery=90.0, timestamp=(base + timedelta(seconds=0)).isoformat()),
        _payload(20, battery=85.0, timestamp=(base + timedelta(seconds=30)).isoformat()),
        _payload(20, battery=80.0, timestamp=(base + timedelta(seconds=60)).isoformat()),
    ]

    response = await client.post("/api/v1/telemetry/batch", json=batch)
    assert response.status_code == 200

    result = await db.execute(
        select(Telemetry).where(Telemetry.robot_id == 20).order_by(Telemetry.timestamp)
    )
    stored = list(result.scalars().all())

    assert len(stored) == 3
    times = [t.timestamp for t in stored]
    assert len(set(times)) == 3, f"batch collapsed onto identical timestamps: {times}"


@pytest.mark.asyncio
async def test_device_timestamp_is_honoured(client: AsyncClient, db):
    """A backdated reading is stored at its reported time, not on arrival.

    This is what makes store-and-forward work: a robot uploading readings it
    buffered through a network outage reports when each was measured.
    """
    measured_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await client.post("/api/v1/telemetry", json=_payload(21, timestamp=measured_at.isoformat()))

    result = await db.execute(select(Telemetry).where(Telemetry.robot_id == 21))
    stored = result.scalars().first()

    assert stored is not None
    delta = abs((stored.timestamp - measured_at).total_seconds())
    assert delta < 2, "device timestamp was overwritten with arrival time"


@pytest.mark.asyncio
async def test_missing_timestamp_falls_back_to_server_time(client: AsyncClient, db):
    """Without a device timestamp the server stamps on arrival."""
    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    await client.post("/api/v1/telemetry", json=_payload(22))

    result = await db.execute(select(Telemetry).where(Telemetry.robot_id == 22))
    stored = result.scalars().first()

    assert stored is not None
    assert stored.timestamp >= before


def test_future_timestamp_is_clamped():
    """A device clock running fast cannot backdate or jump the queue.

    An unclamped future timestamp would sit permanently at the head of "most
    recent", making a dead robot look perpetually alive.
    """
    now = datetime.now(timezone.utc)
    settings = get_settings()
    far_future = now + timedelta(seconds=settings.max_clock_skew_seconds + 600)

    assert resolve_timestamp(far_future, now) == now


def test_small_clock_skew_is_tolerated():
    """Modest skew is accepted rather than rewritten; clocks are never exact."""
    now = datetime.now(timezone.utc)
    slightly_ahead = now + timedelta(seconds=5)

    assert resolve_timestamp(slightly_ahead, now) == slightly_ahead


def test_backdated_timestamp_is_never_clamped():
    """Store-and-forward uploads are legitimately old and must pass through."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=2)

    assert resolve_timestamp(old, now) == old


def test_naive_timestamp_is_treated_as_utc():
    """A device omitting a timezone offset is assumed to mean UTC."""
    now = datetime.now(timezone.utc)
    naive = (now - timedelta(minutes=5)).replace(tzinfo=None)

    resolved = resolve_timestamp(naive, now)
    assert resolved.tzinfo is not None
    assert abs((resolved - (now - timedelta(minutes=5))).total_seconds()) < 1
