"""
Tests for the /api/v1/telemetry endpoints.
"""

from fastapi.testclient import TestClient


def test_ingest_telemetry(client: TestClient, sample_telemetry: dict):
    """Test successful telemetry ingestion."""
    response = client.post("/api/v1/telemetry", json=sample_telemetry)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Telemetry received"
    assert "id" in data


def test_ingest_telemetry_invalid_data(client: TestClient):
    """Test ingestion fails with missing required fields (e.g. robot_id)."""
    invalid_data = {
        "battery": 50.0,
        "temperature": 25.0,
        "speed": 1.0,
    }
    response = client.post("/api/v1/telemetry", json=invalid_data)
    assert response.status_code == 422


def test_get_telemetry_empty(client: TestClient):
    """Test GET /telemetry when the database is empty."""
    response = client.get("/api/v1/telemetry")
    assert response.status_code == 200
    data = response.json()
    assert data == []


def test_get_telemetry_with_data(client: TestClient, sample_telemetry: dict):
    """Test GET /telemetry after ingestion."""
    # Insert 3 rows
    for _ in range(3):
        client.post("/api/v1/telemetry", json=sample_telemetry)

    response = client.get("/api/v1/telemetry?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2  # honors limit
    assert data[0]["robot_id"] == sample_telemetry["robot_id"]
    assert "timestamp" in data[0]
