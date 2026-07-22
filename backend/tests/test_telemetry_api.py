"""
Tests for the /api/v1/telemetry endpoints.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_ingest_telemetry(client: AsyncClient, sample_telemetry: dict):
    """Test successful telemetry ingestion."""
    response = await client.post(
        "/api/v1/telemetry",
        json=sample_telemetry,
        headers={"X-API-Key": "fleet-secret-key-2026"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "id" in data


@pytest.mark.asyncio
async def test_ingest_telemetry_invalid_data(client: AsyncClient):
    """Test ingestion fails with missing required fields (e.g. robot_id)."""
    invalid_data = {
        "battery": 50.0,
        "temperature": 25.0,
        "speed": 1.0,
    }
    response = await client.post(
        "/api/v1/telemetry",
        json=invalid_data,
        headers={"X-API-Key": "fleet-secret-key-2026"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_telemetry_empty(client: AsyncClient):
    """Test GET /telemetry when the database is empty."""
    response = await client.get(
        "/api/v1/telemetry",
        headers={"X-API-Key": "fleet-secret-key-2026"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data == []


@pytest.mark.asyncio
async def test_get_telemetry_with_data(client: AsyncClient, sample_telemetry: dict):
    """Test GET /telemetry after ingestion."""
    headers = {"X-API-Key": "fleet-secret-key-2026"}
    # Insert 3 rows
    for _ in range(3):
        await client.post("/api/v1/telemetry", json=sample_telemetry, headers=headers)

    response = await client.get("/api/v1/telemetry?limit=2", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2  # honors limit
    assert data[0]["robot_id"] == sample_telemetry["robot_id"]
    assert "timestamp" in data[0]
