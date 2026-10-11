"""Retries and redeliveries must not duplicate writes.

Every write path here can legitimately see the same input twice: a client
resending a POST whose response it never got, the worker replaying a batch it
committed but never XACKed, a buffer flush retried after a lost COMMIT
acknowledgement. Each test sends the same thing twice and checks it landed once.
"""

import asyncio
import logging

import orjson
import pytest
from sqlalchemy import func, select

from app import telemetry_buffer
from app.config import get_settings
from app.models import Event, Telemetry, User
from app.repositories.telemetry_repo import TelemetryRepository
from app.schemas import TelemetryCreate
from app.services.telemetry_service import resolve_timestamp
from app.worker import process_batch
from tests.conftest import TestSessionLocal

settings = get_settings()

READING_AT = "2026-07-22T10:00:00.123456Z"


async def _count(db, model) -> int:
    db.expire_all()
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


# ── Telemetry ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resent_reading_is_stored_once(client, db, sample_telemetry):
    reading = {**sample_telemetry, "timestamp": READING_AT}

    first = await client.post("/api/v1/telemetry", json=reading)
    second = await client.post("/api/v1/telemetry", json=reading)

    assert first.status_code == second.status_code == 200
    # The retry gets the answer the original got.
    assert second.json() == first.json()
    assert await _count(db, Telemetry) == 1


@pytest.mark.asyncio
async def test_resent_batch_is_stored_once(client, db, sample_telemetry):
    batch = [
        {**sample_telemetry, "robot_id": 1, "timestamp": READING_AT},
        {**sample_telemetry, "robot_id": 2, "timestamp": READING_AT},
        # A duplicate inside the batch itself is dropped too.
        {**sample_telemetry, "robot_id": 2, "timestamp": READING_AT},
    ]

    for _ in range(2):
        resp = await client.post("/api/v1/telemetry/batch", json=batch)
        assert resp.status_code == 200

    assert await _count(db, Telemetry) == 2


@pytest.mark.asyncio
async def test_worker_replaying_a_batch_writes_nothing_new(db):
    """The worker XACKs after COMMIT. A crash in between leaves the batch
    pending, and it is processed again on restart."""
    messages = [
        (
            f"1-{robot_id}",
            {
                "payload": orjson.dumps(
                    {
                        "robot_id": robot_id,
                        "battery": 90.0,
                        "temperature": 30.0,
                        "speed": 1.0,
                        "timestamp": READING_AT,
                    }
                ).decode()
            },
        )
        for robot_id in (1, 2)
    ]

    async with TestSessionLocal() as session:
        await process_batch(session, messages)
    async with TestSessionLocal() as session:
        await process_batch(session, messages)

    assert await _count(db, Telemetry) == 2


@pytest.mark.asyncio
async def test_failed_buffer_flush_keeps_its_readings(db, monkeypatch, sample_telemetry):
    """A flush that fails must not lose the batch, and retrying it must not
    double-write anything that did land."""
    monkeypatch.setattr(telemetry_buffer, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(telemetry_buffer, "_pending", [])

    data = TelemetryCreate(**{**sample_telemetry, "timestamp": READING_AT})
    rows = [(data, resolve_timestamp(data.timestamp, data.timestamp))]
    await telemetry_buffer.buffer(rows)

    original_insert = TelemetryRepository.insert_many

    async def failing_insert(self, rows):
        raise ConnectionError("database went away")

    monkeypatch.setattr(TelemetryRepository, "insert_many", failing_insert)
    with pytest.raises(ConnectionError):
        await telemetry_buffer.flush()
    assert len(telemetry_buffer._pending) == 1

    monkeypatch.setattr(TelemetryRepository, "insert_many", original_insert)
    assert await telemetry_buffer.flush() == 1
    # The same reading, buffered again (say, a client retry) is not duplicated.
    await telemetry_buffer.buffer(rows)
    await telemetry_buffer.flush()

    assert await _count(db, Telemetry) == 1


# ── Events ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resent_event_with_a_key_is_logged_once(client, db):
    event = {"robot_id": 3, "message": "Entered Restricted Zone!", "idempotency_key": "evt-1"}

    first = await client.post("/api/v1/events", json=event)
    second = await client.post("/api/v1/events", json=event)

    assert first.status_code == second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert await _count(db, Event) == 1


@pytest.mark.asyncio
async def test_event_keys_are_scoped_per_robot(client, db):
    for robot_id in (3, 4):
        resp = await client.post(
            "/api/v1/events",
            json={"robot_id": robot_id, "message": "x", "idempotency_key": "shared"},
        )
        assert resp.status_code == 200

    assert await _count(db, Event) == 2


@pytest.mark.asyncio
async def test_events_without_a_key_are_each_logged(client, db):
    event = {"robot_id": 3, "message": "Completed PATROL mission M-00001"}

    await client.post("/api/v1/events", json=event)
    await client.post("/api/v1/events", json=event)

    assert await _count(db, Event) == 2


# ── Startup ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_bootstrap_creates_one_admin_quietly(db, monkeypatch, caplog):
    """Each uvicorn worker runs the bootstrap at startup, all at once."""
    from app import database
    from app.routes.auth import bootstrap_admin_user

    # The app's pooled engine would keep these four connections bound to this
    # test's event loop, and hand them to the next test on a different one.
    monkeypatch.setattr(database, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(settings, "auth_mode", "required")
    monkeypatch.setattr(settings, "bootstrap_admin_username", "rootadmin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "bootstrap-password")

    with caplog.at_level(logging.ERROR):
        await asyncio.gather(*(bootstrap_admin_user() for _ in range(4)))

    assert await _count(db, User) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
