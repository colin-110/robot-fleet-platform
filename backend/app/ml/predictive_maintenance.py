from collections import Counter, defaultdict
from datetime import datetime, timezone


def _utc_now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clamp(value, lower, upper):
    return max(lower, min(upper, value))


def _format_timestamp(value):
    normalized = _as_utc(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


def _safe_float(value, default=0.0):
    if value is None:
        return default
    return float(value)


def _recent_rows(rows, limit=10):
    if len(rows) <= limit:
        return rows
    return rows[-limit:]


def _average_positive_rate(rows, field_name, *, rising=False):
    samples = []
    for previous, current in zip(rows, rows[1:]):
        previous_time = _as_utc(previous.timestamp)
        current_time = _as_utc(current.timestamp)
        if previous_time is None or current_time is None:
            continue
        elapsed_minutes = (current_time - previous_time).total_seconds() / 60.0
        if elapsed_minutes <= 0:
            continue
        current_value = _safe_float(getattr(current, field_name, None))
        previous_value = _safe_float(getattr(previous, field_name, None))
        delta = current_value - previous_value if rising else previous_value - current_value
        if delta > 0:
            samples.append(delta / elapsed_minutes)
    if not samples:
        return None
    return sum(samples) / len(samples)


def _runtime_remaining_minutes(rows):
    if len(rows) < 2:
        return None

    latest = rows[-1]
    current_battery = _safe_float(latest.battery)
    battery_health = _safe_float(getattr(latest, "battery_health", None), 100.0)
    drain_rate = _average_positive_rate(rows, "battery")

    if drain_rate is None or drain_rate <= 0:
        return None

    effective_battery = current_battery * max(0.1, battery_health / 100.0)
    runtime = effective_battery / drain_rate
    return round(runtime, 1)


def _component_snapshot(row):
    return {
        "battery_health": round(_safe_float(getattr(row, "battery_health", None), 100.0), 2),
        "motor_health": round(_safe_float(getattr(row, "motor_health", None), 100.0), 2),
        "sensor_health": round(_safe_float(getattr(row, "sensor_health", None), 100.0), 2),
        "network_health": round(_safe_float(getattr(row, "network_health", None), 100.0), 2),
    }


def _derive_status(latest, *, age_seconds):
    latest_status = (getattr(latest, "status", None) or "").upper()
    battery = _safe_float(latest.battery)
    temperature = _safe_float(latest.temperature)

    if age_seconds > 300:
        return None
    if latest_status == "DEAD" or battery <= 5 or temperature >= 95:
        return "DEAD"
    if age_seconds > 60:
        return "OFFLINE"
    if latest_status == "CHARGING":
        return "CHARGING"
    if temperature >= 80:
        return "OVERHEATING"
    if battery <= 20:
        return "LOW POWER"
    return "ACTIVE"


def summarize_robot_history(rows, *, now=None):
    if not rows:
        return None

    ordered_rows = sorted(
        rows,
        key=lambda row: (_as_utc(row.timestamp) or _utc_now(), row.id),
    )
    latest = ordered_rows[-1]
    latest_seen = _as_utc(latest.timestamp) or _utc_now()
    current_time = now or _utc_now()
    age_seconds = (current_time - latest_seen).total_seconds()
    status = _derive_status(latest, age_seconds=age_seconds)

    if status is None:
        return None

    recent_rows = _recent_rows(ordered_rows)
    components = _component_snapshot(latest)

    return {
        "robot_id": latest.robot_id,
        "battery": round(_safe_float(latest.battery), 2),
        "temperature": round(_safe_float(latest.temperature), 2),
        "speed": round(_safe_float(latest.speed), 2),
        "status": status,
        "mission_id": getattr(latest, "mission_id", None),
        "mission_type": getattr(latest, "mission_type", None),
        "mission_progress": round(_safe_float(getattr(latest, "mission_progress", None), 0.0), 1)
        if getattr(latest, "mission_id", None)
        else None,
        "mission_start_time": _format_timestamp(getattr(latest, "mission_start_time", None)),
        "last_seen": _format_timestamp(latest_seen),
        "runtime_remaining_minutes": _runtime_remaining_minutes(recent_rows),
        "x": round(_safe_float(getattr(latest, "x", None)), 2),
        "y": round(_safe_float(getattr(latest, "y", None)), 2),
        **components,
    }


def _risk_level(failure_risk):
    if failure_risk >= 85:
        return "CRITICAL"
    if failure_risk >= 65:
        return "HIGH"
    if failure_risk >= 35:
        return "MEDIUM"
    return "LOW"


def build_predictive_maintenance(rows):
    if not rows:
        return None

    ordered_rows = sorted(
        rows,
        key=lambda row: (_as_utc(row.timestamp) or _utc_now(), row.id),
    )
    latest = ordered_rows[-1]
    recent_rows = _recent_rows(ordered_rows)
    battery = round(_safe_float(latest.battery), 2)
    temperature = round(_safe_float(latest.temperature), 2)
    speed = round(_safe_float(latest.speed), 2)
    components = _component_snapshot(latest)

    battery_drain_rate = _average_positive_rate(recent_rows, "battery") or 0.0
    temperature_rise_rate = _average_positive_rate(
        recent_rows,
        "temperature",
        rising=True,
    ) or 0.0

    reasons = []
    risk = 0.0

    if components["battery_health"] < 75:
        risk += (75 - components["battery_health"]) * 0.75
        reasons.append("battery degradation")
    if battery_drain_rate >= 1.4:
        risk += min(18.0, battery_drain_rate * 6.0)
        reasons.append("rapid battery drain")
    if battery < 18:
        risk += (18 - battery) * 1.2
        reasons.append("low charge reserve")

    if components["motor_health"] < 78:
        risk += (78 - components["motor_health"]) * 0.7
        reasons.append("motor wear")
    if speed > 0.1 and components["motor_health"] < 70:
        risk += 8.0
        reasons.append("reduced drive efficiency")

    if components["sensor_health"] < 80:
        risk += (80 - components["sensor_health"]) * 0.55
        reasons.append("sensor degradation")

    if components["network_health"] < 82:
        risk += (82 - components["network_health"]) * 0.65
        reasons.append("network instability")

    if temperature >= 85:
        risk += (temperature - 84) * 1.4
        reasons.append("high temperature trend")
    elif temperature_rise_rate >= 0.7:
        risk += min(12.0, temperature_rise_rate * 10.0)
        reasons.append("rising temperature trend")

    if (getattr(latest, "status", "") or "").upper() == "DEAD":
        risk = 100.0
        reasons = ["permanent failure state"]

    if not reasons:
        reasons.append("within normal operating range")

    failure_risk = int(round(_clamp(risk, 0.0, 100.0)))

    return {
        "robot_id": latest.robot_id,
        "failure_risk": failure_risk,
        "risk_level": _risk_level(failure_risk),
        "reasons": list(dict.fromkeys(reasons)),
        "battery": battery,
        "temperature": temperature,
        "speed": speed,
        "battery_drain_rate_per_min": round(battery_drain_rate, 3),
        "temperature_rise_rate_per_min": round(temperature_rise_rate, 3),
        "runtime_remaining_minutes": _runtime_remaining_minutes(recent_rows),
        **components,
    }


def build_fleet_analytics(rows):
    if not rows:
        return {
            "fleet_health_trend": [],
            "battery_distribution": [],
            "temperature_distribution": [],
            "mission_completion_count": [],
            "robot_status_breakdown": [],
        }

    ordered_rows = sorted(
        rows,
        key=lambda row: (_as_utc(row.timestamp) or _utc_now(), row.id),
    )
    trend_buckets = defaultdict(list)

    for row in ordered_rows:
        timestamp = _as_utc(row.timestamp) or _utc_now()
        bucket = timestamp.replace(second=0, microsecond=0)
        trend_buckets[bucket].append(row)

    fleet_health_trend = []
    for bucket, bucket_rows in sorted(trend_buckets.items()):
        if not bucket_rows:
            continue
        avg_battery = sum(_safe_float(row.battery) for row in bucket_rows) / len(bucket_rows)
        avg_temperature = sum(_safe_float(row.temperature) for row in bucket_rows) / len(bucket_rows)
        avg_component_health = sum(
            (
                _safe_float(getattr(row, "battery_health", None), 100.0)
                + _safe_float(getattr(row, "motor_health", None), 100.0)
                + _safe_float(getattr(row, "sensor_health", None), 100.0)
                + _safe_float(getattr(row, "network_health", None), 100.0)
            )
            / 4.0
            for row in bucket_rows
        ) / len(bucket_rows)
        score = _clamp(avg_component_health * 0.55 + avg_battery * 0.30 - max(0.0, avg_temperature - 55.0) * 1.1, 0.0, 100.0)
        fleet_health_trend.append(
            {
                "timestamp": _format_timestamp(bucket),
                "health_score": round(score, 1),
            }
        )

    battery_ranges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
    temperature_ranges = [(0, 40), (40, 55), (55, 70), (70, 85), (85, 200)]

    latest_by_robot = {}
    for row in ordered_rows:
        latest_by_robot[row.robot_id] = row

    battery_distribution = []
    for lower, upper in battery_ranges:
        label = f"{lower}-{upper - 1 if upper < 101 else 100}%"
        count = sum(lower <= _safe_float(row.battery) < upper for row in latest_by_robot.values())
        battery_distribution.append({"range": label, "count": count})

    temperature_distribution = []
    for lower, upper in temperature_ranges:
        suffix = f"{upper - 1}C" if upper < 200 else "95C+"
        label = f"{lower}-{suffix}"
        count = sum(lower <= _safe_float(row.temperature) < upper for row in latest_by_robot.values())
        temperature_distribution.append({"range": label, "count": count})

    completed_missions = {}
    for row in ordered_rows:
        mission_id = getattr(row, "mission_id", None)
        mission_type = getattr(row, "mission_type", None)
        progress = getattr(row, "mission_progress", None)
        if mission_id and mission_type and progress is not None and _safe_float(progress) >= 100.0:
            completed_missions[mission_id] = mission_type

    mission_counter = Counter(completed_missions.values())
    mission_completion_count = [
        {"mission_type": mission_type, "count": mission_counter.get(mission_type, 0)}
        for mission_type in ["PATROL", "DELIVERY", "INSPECTION"]
    ]

    now = _utc_now()
    robot_status_breakdown = Counter()
    grouped_rows = defaultdict(list)
    for row in ordered_rows:
        grouped_rows[row.robot_id].append(row)

    for robot_id, robot_rows in grouped_rows.items():
        summary = summarize_robot_history(robot_rows, now=now)
        if summary is not None:
            robot_status_breakdown[summary["status"]] += 1

    return {
        "fleet_health_trend": fleet_health_trend[-20:],
        "battery_distribution": battery_distribution,
        "temperature_distribution": temperature_distribution,
        "mission_completion_count": mission_completion_count,
        "robot_status_breakdown": [
            {"status": status, "count": robot_status_breakdown.get(status, 0)}
            for status in ["ACTIVE", "LOW POWER", "OVERHEATING", "OFFLINE", "CHARGING", "DEAD"]
        ],
    }
