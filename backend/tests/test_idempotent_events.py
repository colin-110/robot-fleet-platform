"""Event writes are idempotent: a resent event is stored once."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import Event
from app.websocket_manager import manager
from tests.conftest import TestSessionLocal

WHEN = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []

    async def capture(payload, *_args, **_kwargs):
        sent.append(payload)

    monkeypatch.setattr(manager, "broadcast", capture)
    return sent


async def event_count() -> int:
    async with TestSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(Event))).scalar_one()


@pytest.mark.asyncio
async def test_resent_event_is_stored_and_broadcast_once(client, broadcasts):
    body = {"robot_id": 1, "message": "Mission complete", "timestamp": WHEN}
    first = await client.post("/api/v1/events", json=body)
    second = await client.post("/api/v1/events", json=body)
    assert first.json()["message"] == "Event logged"
    assert second.json() == {"message": "Event already received"}
    assert await event_count() == 1
    assert len(broadcasts) == 1
    assert broadcasts[0]["timestamp"] == "2026-11-01T12:00:00Z"


@pytest.mark.asyncio
async def test_different_events_at_the_same_time_are_both_kept(client, broadcasts):
    for message in ("Low battery", "Obstacle detected"):
        await client.post(
            "/api/v1/events", json={"robot_id": 1, "message": message, "timestamp": WHEN}
        )
    assert await event_count() == 2


@pytest.mark.asyncio
async def test_event_without_a_timestamp_uses_the_server_time(client, broadcasts):
    response = await client.post("/api/v1/events", json={"robot_id": 1, "message": "Started"})
    assert response.json()["message"] == "Event logged"
    assert await event_count() == 1


@pytest.mark.asyncio
async def test_overlong_messages_are_rejected(client, broadcasts):
    response = await client.post("/api/v1/events", json={"robot_id": 1, "message": "x" * 513})
    assert response.status_code == 422
