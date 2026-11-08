from datetime import datetime, timedelta, timezone

from app.intelligence import anomaly, fleet
from app.intelligence.fleet import RobotModel
from app.schemas import TelemetryCreate
from app.services.telemetry_service import store_readings
from tests.conftest import TestSessionLocal

NOW = 1_000_000.0


def model(temperature, drain_per_min=None, age=0.0, speed=1.0) -> RobotModel:
    m = RobotModel()
    m.last = {"t": NOW - age, "temperature": temperature, "speed": speed}
    if drain_per_min is not None:
        m.battery.update(NOW - 60, 80.0)
        m.battery.x = [80.0, -drain_per_min / 60]
        m.battery.p = [[0.25, 0.0], [0.0, 0.008**2]]  # settled, as live filters are
    return m


def test_the_outlier_stands_out_and_the_rest_do_not():
    models = {i: model(35.0 + 0.3 * (i % 3)) for i in range(10)}
    models[99] = model(48.0)
    result = anomaly.scores(models, NOW)
    assert result[99].feature == "temperature" and result[99].z > 8
    assert max(result[i].score for i in range(10)) < 1.5


def test_a_hot_day_for_everyone_is_not_an_anomaly():
    models = {i: model(47.0 + 0.2 * (i % 4)) for i in range(10)}
    assert max(a.score for a in anomaly.scores(models, NOW).values()) < 1.5


def test_one_outlier_does_not_hide_another():
    # With a mean and standard deviation, two outliers inflate the spread
    # enough to hide each other. The median and MAD are not moved.
    models = {i: model(35.0) for i in range(10)}
    models[98], models[99] = model(60.0), model(55.0)
    result = anomaly.scores(models, NOW)
    assert result[98].z > 10 and result[99].z > 10


def test_drain_is_compared_too():
    models = {i: model(35.0, drain_per_min=0.5) for i in range(8)}
    models[99] = model(35.0, drain_per_min=3.0)
    assert anomaly.scores(models, NOW)[99].feature == "drain"


def test_drain_is_compared_only_among_moving_robots():
    # Most of the fleet drives; three idle robots drain far less. Comparing
    # them would flag the idle ones, which says nothing about either.
    models = {i: model(35.0, drain_per_min=2.5) for i in range(8)}
    for i in (97, 98, 99):
        models[i] = model(35.0, drain_per_min=0.1, speed=0.0)
    scores = anomaly.scores(models, NOW)
    assert all(scores[i].feature == "temperature" for i in (97, 98, 99))
    assert max(a.score for a in scores.values()) < 1.5


def test_too_few_peers_or_stale_readings_say_nothing():
    assert anomaly.scores({i: model(35.0) for i in range(3)}, NOW) == {}
    stale = {i: model(35.0, age=600) for i in range(10)}
    assert anomaly.scores(stale, NOW) == {}


async def test_fleet_status_reports_the_score(client):
    now = datetime.now(timezone.utc)
    readings = [
        TelemetryCreate(
            robot_id=40 + i,
            battery=80.0,
            temperature=70.0 if i == 0 else 35.0 + 0.1 * i,
            speed=1.0,
            timestamp=now - timedelta(seconds=2),
        )
        for i in range(8)
    ]
    async with TestSessionLocal() as db:
        await store_readings(db, readings)
    fleet_status = (await client.get("/api/v1/robots/status")).json()
    by_id = {r["robot_id"]: r for r in fleet_status}
    assert by_id[40]["anomaly"]["feature"] == "temperature"
    assert by_id[40]["anomaly"]["score"] > 10
    assert by_id[41]["anomaly"]["score"] < 2
    assert fleet.get(40) is not None


def test_a_drain_the_filter_is_unsure_of_is_left_out():
    models = {i: model(35.0, drain_per_min=2.5) for i in range(8)}
    restarted = model(35.0, drain_per_min=60.0)
    restarted.battery.p = [[0.25, 0.0], [0.0, 0.05**2]]  # just reset
    models[99] = restarted
    assert anomaly.scores(models, NOW)[99].feature == "temperature"


def test_ordinary_differences_in_drain_are_not_anomalies():
    # Every robot drains 2.5 % a minute but one, at 2.9: within 10 % of the
    # median spread floor, so not an outlier, though the MAD here is zero.
    models = {i: model(35.0, drain_per_min=2.5) for i in range(8)}
    models[99] = model(35.0, drain_per_min=2.9)
    assert anomaly.scores(models, NOW)[99].score < 2
