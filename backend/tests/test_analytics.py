"""
Tests for fleet analytics generation.

These exercise Postgres-specific SQL (``date_trunc``, ``INTERVAL`` arithmetic)
that SQLite cannot run — which is why the suite targets a real Postgres test
database. See ``conftest.py``.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_fleet_analytics_empty(client: AsyncClient):
    """With no telemetry, every aggregate is present but zeroed.

    The endpoint returns a fixed set of buckets rather than an empty list so
    the dashboard's charts keep a stable shape instead of collapsing.
    """
    response = await client.get("/api/v1/analytics/fleet")
    assert response.status_code == 200
    data = response.json()

    assert data["fleet_health_trend"] == []
    assert all(bucket["count"] == 0 for bucket in data["battery_distribution"])
    assert all(bucket["count"] == 0 for bucket in data["temperature_distribution"])
    assert all(entry["count"] == 0 for entry in data["mission_completion_count"])
    assert all(entry["count"] == 0 for entry in data["robot_status_breakdown"])


@pytest.mark.asyncio
async def test_fleet_analytics_with_data(
    client: AsyncClient, sample_telemetry: dict, sample_telemetry_low_battery: dict
):
    """Test analytics endpoint with multiple telemetry rows."""
    await client.post("/api/v1/telemetry", json=sample_telemetry)
    await client.post("/api/v1/telemetry", json=sample_telemetry_low_battery)

    response = await client.get("/api/v1/analytics/fleet")
    assert response.status_code == 200
    data = response.json()

    assert any(b["count"] > 0 for b in data["battery_distribution"])
    assert any(b["count"] > 0 for b in data["temperature_distribution"])

    # sample_telemetry carries a PATROL mission at 100% progress.
    missions = {m["mission_type"]: m["count"] for m in data["mission_completion_count"]}
    assert missions.get("PATROL", 0) > 0

    statuses = {s["status"]: s["count"] for s in data["robot_status_breakdown"]}
    assert statuses.get("ACTIVE", 0) == 1
    assert statuses.get("LOW POWER", 0) == 1


@pytest.mark.asyncio
async def test_fleet_health_trend_buckets_by_minute(client: AsyncClient, sample_telemetry: dict):
    """Health trend groups readings into per-minute buckets with a 0-100 score."""
    await client.post("/api/v1/telemetry", json=sample_telemetry)

    response = await client.get("/api/v1/analytics/fleet")
    assert response.status_code == 200
    trend = response.json()["fleet_health_trend"]

    assert len(trend) == 1
    point = trend[0]
    assert point["timestamp"].endswith("Z")
    # Bucketed to the minute, so seconds are always zero.
    assert point["timestamp"][17:19] == "00"
    assert 0.0 <= point["health_score"] <= 100.0
