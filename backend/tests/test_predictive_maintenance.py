"""
Tests for predictive maintenance risk scoring.
"""

from fastapi.testclient import TestClient


def test_predictive_maintenance_empty(client: TestClient):
    """Test maintenance endpoint when database is empty."""
    response = client.get("/api/v1/robots/predictive-maintenance")
    assert response.status_code == 200
    assert response.json() == []


def test_predictive_maintenance_healthy(client: TestClient, sample_telemetry: dict):
    """Test risk scoring for a healthy robot."""
    client.post("/api/v1/telemetry", json=sample_telemetry)

    response = client.get("/api/v1/robots/predictive-maintenance")
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 1
    robot = data[0]
    
    assert robot["robot_id"] == 1
    assert robot["risk_level"] == "LOW"
    assert "failure_risk" in robot
    assert isinstance(robot["reasons"], list)


def test_predictive_maintenance_high_risk(
    client: TestClient, sample_telemetry_overheating: dict
):
    """Test risk scoring for an overheating robot."""
    client.post("/api/v1/telemetry", json=sample_telemetry_overheating)

    response = client.get("/api/v1/robots/predictive-maintenance")
    assert response.status_code == 200
    data = response.json()

    assert len(data) == 1
    robot = data[0]
    
    assert robot["robot_id"] == 3
    assert robot["risk_level"] in ("HIGH", "CRITICAL")
    assert any("temperature" in reason.lower() for reason in robot["reasons"])
