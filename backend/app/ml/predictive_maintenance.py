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


def _recent_rows(rows, limit=6):

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

        elapsed_minutes = (
            current_time - previous_time
        ).total_seconds() / 60.0

        if elapsed_minutes <= 0:
            continue

        if rising:
            delta = getattr(current, field_name) - getattr(previous, field_name)
        else:
            delta = getattr(previous, field_name) - getattr(current, field_name)

        if delta > 0:
            samples.append(delta / elapsed_minutes)

    if not samples:
        return None

    return sum(samples) / len(samples)


def _runtime_remaining_minutes(rows):

    if len(rows) < 2:
        return None

    current_battery = rows[-1].battery
    drain_rate = _average_positive_rate(rows, "battery")

    if drain_rate is None or drain_rate <= 0:
        return None

    return round(current_battery / drain_rate, 1)


def _health_score(
    battery,
    temperature,
    battery_drain_rate,
    temperature_rise_rate
):

    score = 100.0

    score -= max(0.0, 100.0 - battery) * 0.35
    score -= min(35.0, battery_drain_rate * 18.0)
    score -= max(0.0, temperature - 40.0) * 1.15
    score -= min(25.0, temperature_rise_rate * 12.0)

    return _clamp(score, 0.0, 100.0)


def _risk_level(failure_risk):

    if failure_risk >= 85:
        return "CRITICAL"

    if failure_risk >= 60:
        return "HIGH"

    if failure_risk >= 30:
        return "MEDIUM"

    return "LOW"


def summarize_robot_history(rows, *, now=None):

    if not rows:
        return None

    ordered_rows = sorted(
        rows,
        key=lambda row: (
            _as_utc(row.timestamp) or _utc_now(),
            row.id
        )
    )

    latest = ordered_rows[-1]

    latest_seen = _as_utc(latest.timestamp) or _utc_now()

    current_time = now or _utc_now()

    age_seconds = (
        current_time - latest_seen
    ).total_seconds()

    if age_seconds > 300:
        return None

    if age_seconds > 60:
        status = "OFFLINE"
    elif latest.battery < 5 or latest.temperature > 95:
        status = "DEAD"
    elif latest.battery < 25:
        status = "LOW POWER"
    elif latest.temperature > 70:
        status = "OVERHEATING"
    else:
        status = "ACTIVE"

    runtime_remaining_minutes = _runtime_remaining_minutes(
        _recent_rows(ordered_rows)
    )

    return {
        "robot_id": latest.robot_id,
        "battery": round(float(latest.battery), 2),
        "temperature": round(float(latest.temperature), 2),
        "speed": round(float(latest.speed), 2),
        "status": status,
        "last_seen": _format_timestamp(latest_seen),
        "runtime_remaining_minutes": runtime_remaining_minutes
    }


def build_predictive_maintenance(rows):

    if not rows:
        return None

    ordered_rows = sorted(
        rows,
        key=lambda row: (
            _as_utc(row.timestamp) or _utc_now(),
            row.id
        )
    )

    recent_rows = _recent_rows(ordered_rows)

    latest = ordered_rows[-1]

    battery = round(float(latest.battery), 2)
    temperature = round(float(latest.temperature), 2)

    battery_drain_rate = _average_positive_rate(recent_rows, "battery") or 0.0
    temperature_rise_rate = _average_positive_rate(
        recent_rows,
        "temperature",
        rising=True
    ) or 0.0

    runtime_remaining_minutes = _runtime_remaining_minutes(recent_rows)

    health_score = _health_score(
        battery,
        temperature,
        battery_drain_rate,
        temperature_rise_rate
    )

    failure_risk = int(
        round(
            _clamp(
                100.0 - health_score,
                0.0,
                100.0
            )
        )
    )

    reasons = []

    if battery < 15:
        reasons.append("low battery")

    if battery_drain_rate >= 1.5:
        reasons.append("rapid battery drain")

    if temperature >= 85:
        reasons.append("high temperature")
    elif temperature >= 70:
        reasons.append("elevated temperature")

    if temperature_rise_rate >= 1.0:
        reasons.append("rising temperature")

    if health_score < 65:
        reasons.append("declining health score")

    if not reasons:
        reasons.append("within normal operating range")

    return {
        "robot_id": latest.robot_id,
        "failure_risk": failure_risk,
        "risk_level": _risk_level(failure_risk),
        "reasons": reasons,
        "battery": battery,
        "temperature": temperature,
        "battery_drain_rate_per_min": round(battery_drain_rate, 3),
        "temperature_rise_rate_per_min": round(temperature_rise_rate, 3),
        "health_score": round(health_score, 1),
        "runtime_remaining_minutes": runtime_remaining_minutes
    }
