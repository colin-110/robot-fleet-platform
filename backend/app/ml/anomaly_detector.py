import pandas as pd

from sklearn.ensemble import IsolationForest


def detect_anomalies(telemetry_rows):

    if len(telemetry_rows) < 10:

        return []

    data = []

    for row in telemetry_rows:

        data.append({

            "robot_id": row.robot_id,

            "battery": row.battery,

            "temperature": row.temperature,

            "speed": row.speed
        })

    df = pd.DataFrame(data)

    features = df[
        ["battery", "temperature", "speed"]
    ]

    model = IsolationForest(
        contamination=0.1,
        random_state=42
    )

    df["anomaly"] = model.fit_predict(features)

    # IsolationForest: higher decision_function => more normal.
    # Convert to a "risk" score where higher => more anomalous.
    decision = model.decision_function(features)
    risk_raw = (-decision).astype(float)

    score_min = float(risk_raw.min()) if len(risk_raw) else 0.0
    score_max = float(risk_raw.max()) if len(risk_raw) else 1.0
    denom = (score_max - score_min) if (score_max - score_min) != 0 else 1.0
    df["anomaly_score"] = (risk_raw - score_min) / denom

    anomalies = df[df["anomaly"] == -1]

    results = []

    for _, row in anomalies.iterrows():

        anomaly_type = "UNKNOWN"

        severity = "LOW"

        reason = "Model flagged unusual telemetry pattern."

        recommended_action = "Inspect robot and validate sensors."

        # OVERHEATING

        if row["temperature"] > 85:

            anomaly_type = "OVERHEATING"

            severity = "CRITICAL"

            reason = "Temperature is above the critical threshold."

            recommended_action = "Stop robot, check cooling system, and reduce workload."

        elif row["temperature"] > 70:

            anomaly_type = "HIGH TEMPERATURE"

            severity = "HIGH"

            reason = "Temperature is above the normal operating range."

            recommended_action = "Reduce load and schedule cooling system inspection."

        # BATTERY

        elif row["battery"] < 10:

            anomaly_type = "BATTERY FAILURE"

            severity = "CRITICAL"

            reason = "Battery level is critically low and may indicate failure."

            recommended_action = "Return to dock immediately and run battery diagnostics."

        elif row["battery"] < 25:

            anomaly_type = "LOW BATTERY"

            severity = "HIGH"

            reason = "Battery is below safe operating threshold."

            recommended_action = "Route robot to charging station and throttle nonessential tasks."

        # SPEED

        elif row["speed"] > 4.5:

            anomaly_type = "ABNORMAL SPEED"

            severity = "MEDIUM"

            reason = "Speed exceeds typical operating envelope."

            recommended_action = "Verify navigation stack and check for localization drift."

        results.append({

            "robot_id": int(row["robot_id"]),

            "battery": round(
                float(row["battery"]),
                2
            ),

            "temperature": round(
                float(row["temperature"]),
                2
            ),

            "speed": round(
                float(row["speed"]),
                2
            ),

            "anomaly_score": round(
                float(row["anomaly_score"]),
                3
            ),

            "anomaly_type": anomaly_type,

            "severity": severity,

            "reason": reason,

            "recommended_action": recommended_action
        })

    return results
