"""
Tests for fleet analytics generation.
"""

from fastapi.testclient import TestClient


def test_fleet_analytics_empty(client: TestClient):
    """Test analytics endpoint when database is empty."""
    response = client.get("/api/v1/analytics/fleet")
    assert response.status_code == 200
    data = response.json()
    
    assert data["fleet_health_trend"] == []
    assert data["battery_distribution"] == []
    assert data["temperature_distribution"] == []


def test_fleet_analytics_with_data(
    client: TestClient, sample_telemetry: dict, sample_telemetry_low_battery: dict
):
    """Test analytics endpoint with multiple telemetry rows."""
    client.post("/api/v1/telemetry", json=sample_telemetry)
    client.post("/api/v1/telemetry", json=sample_telemetry_low_battery)

    response = client.get("/api/v1/analytics/fleet")
    assert response.status_code == 200
    data = response.json()

    # Check distributions are populated
    assert any(b["count"] > 0 for b in data["battery_distribution"])
    assert any(b["count"] > 0 for b in data["temperature_distribution"])
    
    # Check mission completion
    missions = {m["mission_type"]: m["count"] for m in data["mission_completion_count"]}
    assert missions.get("PATROL", 0) > 0
    
    # Check status breakdown
    statuses = {s["status"]: s["count"] for s in data["robot_status_breakdown"]}
    assert statuses.get("ACTIVE", 0) == 1
    assert statuses.get("LOW POWER", 0) == 1
