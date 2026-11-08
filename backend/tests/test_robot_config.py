"""Desired and reported robot settings."""

import asyncio

import pytest

from app.mqtt import config as mqtt_config
from app.mqtt import wire
from app.mqtt.ingest import Delivery, IngestConsumer
from tests.conftest import TestSessionLocal
from tests.test_mqtt_ingest import FakeClient


class RecordingPublisher(mqtt_config.ConfigPublisher):
    def __init__(self):
        super().__init__(FakeClient(), session_factory=TestSessionLocal)

    def sent(self):
        return [
            (t, wire.decode_config(p), qos, retain) for t, p, qos, retain in self.client.published
        ]


@pytest.fixture
def publisher(monkeypatch):
    recording = RecordingPublisher()
    monkeypatch.setattr(mqtt_config, "publisher", recording)
    return recording


async def test_a_robot_without_settings(client):
    response = await client.get("/api/v1/robots/5/config")
    assert response.status_code == 200
    assert response.json() == {
        "robot_id": 5,
        "desired": {},
        "version": 0,
        "updated_at": None,
        "reported": None,
        "reported_rejected": None,
        "reported_version": None,
        "reported_at": None,
        "in_sync": True,
    }


async def test_setting_pushes_a_new_version(client, publisher):
    first = await client.put("/api/v1/robots/5/config", json={"values": {"report_interval_s": 5}})
    assert first.status_code == 200
    assert (first.json()["version"], first.json()["desired"]) == (1, {"report_interval_s": 5.0})
    assert first.json()["in_sync"] is False

    second = await client.put("/api/v1/robots/5/config", json={"values": {"report_interval_s": 2}})
    assert second.json()["version"] == 2
    assert publisher.sent() == [
        ("fleet/5/config", (1, {"report_interval_s": 5.0}), 1, True),
        ("fleet/5/config", (2, {"report_interval_s": 2.0}), 1, True),
    ]


@pytest.mark.parametrize(
    "values", [{"warp": 9}, {"report_interval_s": 0}, {"report_interval_s": "fast"}]
)
async def test_bad_settings_are_refused(client, publisher, values):
    response = await client.put("/api/v1/robots/5/config", json={"values": values})
    assert response.status_code == 422
    assert set(response.json()["detail"]["rejected"]) == set(values)
    assert publisher.sent() == []


async def test_the_robots_report_is_recorded(client, publisher):
    await client.put("/api/v1/robots/5/config", json={"values": {"report_interval_s": 5}})
    consumer = IngestConsumer(session_factory=TestSessionLocal)
    consumer.client = FakeClient()
    consumer.epoch = 1
    report = wire.encode_reported(1, {"report_interval_s": 5.0}, {})
    await consumer.process([Delivery(5, "config/reported", report, 1, 1, 1)])

    body = (await client.get("/api/v1/robots/5/config")).json()
    assert body["reported"] == {"report_interval_s": 5.0}
    assert body["reported_version"] == 1
    assert body["in_sync"] is True
    assert consumer.client.acked == [1]


async def test_a_bad_report_is_dropped(client):
    consumer = IngestConsumer(session_factory=TestSessionLocal)
    consumer.client = FakeClient()
    consumer.epoch = 1
    await consumer.process([Delivery(5, "config/reported", b"[]", 1, 1, 1)])
    assert consumer.stats["rejected"] == 1
    assert consumer.client.acked == [1]


async def test_every_desired_setting_is_republished_on_start(client, publisher):
    await client.put("/api/v1/robots/5/config", json={"values": {"report_interval_s": 5}})
    await client.put("/api/v1/robots/6/config", json={"values": {"report_interval_s": 9}})
    publisher.client.published.clear()

    connected = asyncio.Event()
    connected.set()
    await publisher.republish_all(connected)
    assert sorted(t for t, *_ in publisher.sent()) == ["fleet/5/config", "fleet/6/config"]
