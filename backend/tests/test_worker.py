import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.worker import process_batch
from app.models import Telemetry

@pytest.mark.asyncio
async def test_process_batch_success():
    # Mock db session
    mock_db = AsyncMock()
    
    # Mock messages from redis
    messages = [
        (b'12345-0', {
            b'robot_id': b'1', b'battery': b'100.0', b'temperature': b'25.0',
            b'speed': b'1.0', b'timestamp': b'2026-07-22T00:00:00Z'
        })
    ]
    
    last_id = await process_batch(mock_db, messages)
    assert last_id == b'12345-0'
    mock_db.commit.assert_called_once()
    assert mock_db.add.called

@pytest.mark.asyncio
async def test_process_batch_empty():
    mock_db = AsyncMock()
    last_id = await process_batch(mock_db, [])
    assert last_id is None
    mock_db.commit.assert_not_called()

@pytest.mark.asyncio
async def test_process_batch_invalid_message():
    mock_db = AsyncMock()
    # Missing required fields
    messages = [
        (b'12345-0', {
            b'robot_id': b'invalid',
        })
    ]
    last_id = await process_batch(mock_db, messages)
    # Should skip invalid message and still return last_id
    assert last_id == b'12345-0'
    mock_db.commit.assert_called_once() # Commit is called even if batch is empty of valid models
