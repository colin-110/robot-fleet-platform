import orjson
import pytest
from unittest.mock import AsyncMock

from app.worker import process_batch


def _msg(msg_id: str, payload: dict):
    """Build a Redis-stream entry the way XREADGROUP returns it."""
    return (msg_id, {"payload": orjson.dumps(payload).decode("utf-8")})


@pytest.mark.asyncio
async def test_process_batch_inserts_telemetry():
    session = AsyncMock()
    messages = [
        _msg("12345-0", {
            "robot_id": 1, "battery": 100.0, "temperature": 25.0,
            "speed": 1.0, "timestamp": "2026-07-22T00:00:00Z",
        }),
        _msg("12345-1", {
            "robot_id": 2, "battery": 80.0, "temperature": 30.0,
            "speed": 0.5, "timestamp": "2026-07-22T00:00:01Z",
        }),
    ]

    message_ids = await process_batch(session, messages)

    # Every message id is returned so the caller can XACK the whole batch.
    assert message_ids == ["12345-0", "12345-1"]
    # Both valid telemetry rows are bulk-inserted in a single committed write.
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_batch_empty():
    session = AsyncMock()

    message_ids = await process_batch(session, [])

    assert message_ids == []
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_process_batch_skips_non_telemetry():
    """Event/command payloads (no `battery` field) are acked but not inserted."""
    session = AsyncMock()
    messages = [
        ("99-0", {"payload": orjson.dumps(
            {"type": "EVENT", "robot_id": 5, "message": "Entered zone"}
        ).decode("utf-8")}),
    ]

    message_ids = await process_batch(session, messages)

    assert message_ids == ["99-0"]
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_process_batch_skips_undecodable_payload():
    """A corrupt payload is acknowledged (so it isn't reprocessed) but skipped."""
    session = AsyncMock()
    messages = [("77-0", {"payload": "not-valid-json{"})]

    message_ids = await process_batch(session, messages)

    assert message_ids == ["77-0"]
    session.execute.assert_not_called()
