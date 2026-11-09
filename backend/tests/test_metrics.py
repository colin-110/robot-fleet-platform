"""/metrics: protected by the master key, and reporting what is running."""

from datetime import datetime, timedelta, timezone

import pytest
from prometheus_client import REGISTRY

from app import metrics
from app.config import get_settings
from tests.test_mqtt_ingest import FakeClient, delivery, reading

settings = get_settings()


@pytest.fixture
def no_sources():
    saved = dict(metrics.sources)
    metrics.sources.clear()
    yield metrics.sources
    metrics.sources.clear()
    metrics.sources.update(saved)


def sample(name: str, **labels) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else value


@pytest.mark.asyncio
async def test_metrics_need_the_master_key(client):
    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/metrics", headers={"X-API-Key": "wrong"})).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["X-API-Key", "Authorization"])
async def test_metrics_accept_the_key_or_a_bearer_token(client, header):
    key = settings.telemetry_api_key
    value = f"Bearer {key}" if header == "Authorization" else key
    response = await client.get("/metrics", headers={header: value})
    assert response.status_code == 200
    assert "fleetops_websocket_clients" in response.text
    assert "fleetops_robots_online" in response.text


@pytest.mark.asyncio
async def test_ingest_counters_and_durations_are_reported(no_sources, monkeypatch):
    from app.mqtt.ingest import IngestConsumer
    from app.websocket_manager import manager
    from tests.conftest import TestSessionLocal

    async def drop(rows, stream=None):
        pass

    monkeypatch.setattr(manager, "broadcast_batch", drop)
    consumer = IngestConsumer(session_factory=TestSessionLocal)
    consumer.client = FakeClient()
    consumer.epoch = 1
    no_sources["ingest"] = consumer

    batches = sample("fleetops_ingest_batch_seconds_count")
    recent = sample("fleetops_reading_age_seconds_bucket", le="5.0")
    now = datetime.now(timezone.utc)
    fresh = [
        reading(seconds=0, timestamp=(now - timedelta(seconds=2 - i)).isoformat()) for i in range(3)
    ]
    await consumer.process([delivery(7, body, mid=i + 1) for i, body in enumerate(fresh)])
    await consumer.process([delivery(7, fresh[0], mid=9)])

    assert sample("fleetops_ingest_stored_total") == 3
    assert sample("fleetops_ingest_duplicates_total") == 1
    assert sample("fleetops_ingest_batches_total") == 2
    assert sample("fleetops_ingest_queue_depth") == 0
    assert sample("fleetops_ingest_batch_seconds_count") == batches + 2
    # Each was at most two seconds old when it was committed.
    assert sample("fleetops_reading_age_seconds_bucket", le="5.0") == recent + 3


def test_sources_that_are_not_running_are_left_out(no_sources):
    assert REGISTRY.get_sample_value("fleetops_ingest_stored_total") is None
    assert REGISTRY.get_sample_value("fleetops_rollup_runs_total") is None


@pytest.mark.asyncio
async def test_dropped_websocket_frames_are_counted():
    import asyncio

    from app.websocket_manager import CLIENT_QUEUE_MAXSIZE, ConnectionManager

    class Socket:
        pass

    m = ConnectionManager()
    ws = Socket()
    m.active_connections.append(ws)
    m._queues[ws] = asyncio.Queue(maxsize=CLIENT_QUEUE_MAXSIZE)
    for i in range(CLIENT_QUEUE_MAXSIZE + 5):
        m._fan_out({"robot_id": 1, "i": i})
    assert m.frames_dropped == 5
    assert m.queued_frames == CLIENT_QUEUE_MAXSIZE


@pytest.mark.asyncio
async def test_an_alarm_pass_is_timed():
    from app.alarms.engine import AlarmEngine
    from tests.conftest import TestSessionLocal

    before = sample("fleetops_alarm_pass_seconds_count")
    engine = AlarmEngine(session_factory=TestSessionLocal)
    assert engine.open_count is None
    await engine.evaluate()
    assert engine.open_count == 0
    assert sample("fleetops_alarm_pass_seconds_count") == before + 1
