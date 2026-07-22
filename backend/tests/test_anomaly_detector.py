import pytest
from app.ml.anomaly_detector import add_telemetry_and_detect_anomalies, _history

def test_anomaly_detector_temperature_spike():
    _history.clear()
    
    # Send normal temps
    for _ in range(10):
        anomalies = add_telemetry_and_detect_anomalies({"robot_id": 1, "temperature": 40.0, "battery": 100})
        assert not anomalies
        
    # Send spike
    anomalies = add_telemetry_and_detect_anomalies({"robot_id": 1, "temperature": 80.0, "battery": 100})
    assert len(anomalies) == 1
    assert "Temperature spike detected" in anomalies[0]

def test_anomaly_detector_battery_drain():
    _history.clear()
    
    # Send normal battery drops
    batt = 100.0
    for _ in range(9):
        anomalies = add_telemetry_and_detect_anomalies({"robot_id": 2, "temperature": 40.0, "battery": batt})
        assert not anomalies
        batt -= 0.1
        
    # Send steep drop
    anomalies = add_telemetry_and_detect_anomalies({"robot_id": 2, "temperature": 40.0, "battery": batt - 6.0})
    assert len(anomalies) == 1
    assert "Abnormal battery drain detected" in anomalies[0]

def test_anomaly_detector_no_robot_id():
    anomalies = add_telemetry_and_detect_anomalies({"temperature": 40.0, "battery": 100})
    assert not anomalies
