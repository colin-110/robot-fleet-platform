from app.ml.predictive_maintenance import build_predictive_maintenance


def detect_anomalies(telemetry_rows):

    result = build_predictive_maintenance(
        telemetry_rows
    )

    return [result] if result else []
