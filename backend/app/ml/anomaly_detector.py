"""
Statistical anomaly detection for robot telemetry.

This module implements predictive maintenance algorithms to detect
abnormal battery drain and temperature spikes without needing heavy ML dependencies.
"""

from collections import deque
import logging

logger = logging.getLogger(__name__)

# In-memory history for quick anomaly detection
# In production, this would be backed by Redis or TimescaleDB
_history = {}


def add_telemetry_and_detect_anomalies(telemetry_data: dict) -> list[str]:
    """
    Ingests a single telemetry reading, updates historical averages,
    and returns a list of detected anomaly messages if any exist.
    """
    robot_id = telemetry_data.get("robot_id")
    if not robot_id:
        return []

    if robot_id not in _history:
        _history[robot_id] = {
            "temperatures": deque(maxlen=20),
            "batteries": deque(maxlen=20),
            "timestamps": deque(maxlen=20)
        }

    history = _history[robot_id]
    temp = telemetry_data.get("temperature", 0.0)
    batt = telemetry_data.get("battery", 100.0)
    ts = telemetry_data.get("timestamp")

    history["temperatures"].append(temp)
    history["batteries"].append(batt)
    history["timestamps"].append(ts)

    anomalies = []

    # 1. Temperature Spike Detection (Z-Score approximation)
    if len(history["temperatures"]) >= 10:
        temps = list(history["temperatures"])
        recent_temp = temps[-1]
        historical_temps = temps[:-1]
        
        avg_temp = sum(historical_temps) / len(historical_temps)
        # Approximate std_dev
        variance = sum((t - avg_temp) ** 2 for t in historical_temps) / len(historical_temps)
        std_dev = variance ** 0.5
        
        if std_dev > 1.0:
            z_score = (recent_temp - avg_temp) / std_dev
            if z_score > 3.0 and recent_temp > 60.0:
                anomalies.append(f"Temperature spike detected: {recent_temp:.1f}°C (Z-Score: {z_score:.1f})")

    # 2. Abnormal Battery Drain Detection
    if len(history["batteries"]) >= 10:
        batts = list(history["batteries"])
        drain = batts[0] - batts[-1]
        if drain > 5.0:  # Dropped more than 5% in the last ~10 ticks
            anomalies.append(f"Abnormal battery drain detected: {drain:.1f}% over last 10 readings")

    if anomalies:
        logger.warning("Anomalies detected for robot %s: %s", robot_id, anomalies)

    return anomalies
