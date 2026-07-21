import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models import RobotCommand
import asyncio
from datetime import datetime, timezone, timedelta
from app.worker import scan_for_timeouts

TEST_ROBOT_ID = 100

@pytest.mark.asyncio
async def test_command_valid_lifecycle(client: AsyncClient, db: AsyncSession):
    # 1. POST Command -> PENDING
    payload = {
        "command_type": "MOVE_TO",
        "payload": {"x": 10, "y": 20},
        "idempotency_key": "test-key-1"
    }
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    assert resp.status_code == 200
    cmd_data = resp.json()["payload"]
    assert cmd_data["status"] == "PENDING"
    cmd_id = cmd_data["command_id"]
    
    # 2. GET Commands (Simulator poll) -> DISPATCHED
    resp = await client.get(f"/api/v1/commands/{TEST_ROBOT_ID}")
    assert resp.status_code == 200
    cmds = resp.json()
    assert len(cmds) == 1
    assert cmds[0]["id"] == cmd_id
    
    # 3. ACKNOWLEDGED
    resp = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"})
    assert resp.status_code == 200
    assert resp.json()["payload"]["status"] == "ACKNOWLEDGED"
    
    # 4. EXECUTING
    resp = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "EXECUTING"})
    assert resp.status_code == 200
    
    # 5. COMPLETED
    resp = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "COMPLETED", "result": {"success": True}})
    assert resp.status_code == 200
    
    # Verify DB
    stmt = select(RobotCommand).where(RobotCommand.id == cmd_id)
    result = await db.execute(stmt)
    record = result.scalars().first()
    assert record.status == "COMPLETED"
    assert record.result == {"success": True}
    assert record.completed_at is not None

@pytest.mark.asyncio
async def test_command_invalid_transition(client: AsyncClient):
    # PENDING -> COMPLETED should fail
    payload = {"command_type": "TEST", "idempotency_key": "test-key-2"}
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    cmd_id = resp.json()["payload"]["command_id"]
    
    resp = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "COMPLETED"})
    assert resp.status_code == 400
    assert "Invalid transition" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_command_terminal_immutability(client: AsyncClient):
    payload = {"command_type": "TEST", "idempotency_key": "test-key-3"}
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    cmd_id = resp.json()["payload"]["command_id"]
    
    # Cancel it immediately
    resp = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "CANCELLED"})
    assert resp.status_code == 200
    
    # Try to execute
    resp = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "EXECUTING"})
    assert resp.status_code == 400
    assert "terminal state" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_duplicate_idempotency_key(client: AsyncClient):
    payload = {"command_type": "TEST", "idempotency_key": "test-key-dup"}
    resp1 = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    resp2 = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Should return the exact same command_id
    assert resp1.json()["payload"]["command_id"] == resp2.json()["payload"]["command_id"]

@pytest.mark.asyncio
async def test_repeated_same_state_update(client: AsyncClient):
    payload = {"command_type": "TEST", "idempotency_key": "test-key-rep"}
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    cmd_id = resp.json()["payload"]["command_id"]
    
    await client.get(f"/api/v1/commands/{TEST_ROBOT_ID}")
    
    resp1 = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"})
    resp2 = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"})
    
    assert resp1.status_code == 200
    assert resp2.status_code == 200

@pytest.mark.asyncio
async def test_timeout_behavior(client: AsyncClient, db: AsyncSession):
    payload = {"command_type": "TEST", "timeout_seconds": -1} # expires immediately
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    cmd_id = resp.json()["payload"]["command_id"]
    
    # Run timeout scan
    await scan_for_timeouts(db)
    
    # Check DB directly since it's an internal process
    stmt = select(RobotCommand).where(RobotCommand.id == cmd_id)
    result = await db.execute(stmt)
    record = result.scalars().first()
    assert record.status == "TIMEOUT"
    
    # Late ACK should fail due to terminal state
    resp = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"})
    assert resp.status_code == 400
    assert "terminal state" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_concurrent_dispatch(client: AsyncClient):
    payload = {"command_type": "TEST", "idempotency_key": "test-key-conc"}
    await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    
    # Attempt concurrent fetches
    tasks = [
        client.get(f"/api/v1/commands/{TEST_ROBOT_ID}")
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)
    
    # Only one request should actually return the command, others should get empty list
    cmds_returned = 0
    for resp in results:
        if resp.json():
            cmds_returned += len(resp.json())
            
    assert cmds_returned == 1
