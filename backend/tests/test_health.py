"""The /health endpoint reports the database and the MQTT consumer."""

import asyncio

import pytest

from app import main
from app.main import app
from tests.conftest import test_engine


class StubConsumer:
    def __init__(self, connected: bool):
        self.connected = asyncio.Event()
        if connected:
            self.connected.set()


@pytest.fixture
def consumer_state(monkeypatch):
    # The app's pooled engine would hold connections from an earlier test's loop.
    monkeypatch.setattr(main, "engine", test_engine)
    previous = getattr(app.state, "mqtt", None)
    yield
    app.state.mqtt = previous


@pytest.mark.asyncio
async def test_health_without_mqtt(client, consumer_state):
    app.state.mqtt = None
    body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["mqtt"] == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("connected,status", [(True, "ok"), (False, "degraded")])
async def test_health_reports_the_broker_connection(client, consumer_state, connected, status):
    app.state.mqtt = StubConsumer(connected)
    body = (await client.get("/health")).json()
    assert body["status"] == status
    assert body["mqtt"] == ("connected" if connected else "disconnected")
