"""
Tests for the /api/v1/robots/status endpoint and fleet status derivation.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_robot_status_empty(client: AsyncClient):
    """Test status endpoint when database is empty."""
    response = await client.get("/api/v1/robots/status")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_robot_status_derivation(
    client: AsyncClient, sample_telemetry: dict, sample_telemetry_low_battery: dict
):
    """Test status is correctly derived for multiple robots."""
    headers = {"X-API-Key": "fleet-secret-key-2026"}
    await client.post("/api/v1/telemetry", json=sample_telemetry, headers=headers)
    await client.post("/api/v1/telemetry", json=sample_telemetry_low_battery, headers=headers)

    response = await client.get("/api/v1/robots/status")
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 2

    # Check that both robots are returned and have expected status
    robots = {r["robot_id"]: r for r in data}

    r1 = robots[1]
    assert r1["status"] == "ACTIVE"
    assert r1["battery"] == 85.5

    r2 = robots[2]
    assert r2["status"] == "LOW POWER"
    assert r2["battery"] == 8.0
