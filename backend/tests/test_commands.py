import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models import RobotCommand
from app.worker import scan_for_timeouts

TEST_ROBOT_ID = 100


@pytest.mark.asyncio
async def test_command_valid_lifecycle(client: AsyncClient, db: AsyncSession):
    # 1. POST Command -> PENDING
    payload = {
        "command_type": "MOVE_TO",
        "payload": {"x": 10, "y": 20},
        "idempotency_key": "test-key-1",
    }
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    assert resp.status_code == 200
    cmd_data = resp.json()["payload"]
    assert cmd_data["status"] == "PENDING"
    cmd_id = cmd_data["command_id"]

    # 2. Claim (simulator poll) -> DISPATCHED
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim")
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
    resp = await client.patch(
        f"/api/v1/commands/{cmd_id}/status",
        json={"status": "COMPLETED", "result": {"success": True}},
    )
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
async def test_same_idempotency_key_on_two_robots(client: AsyncClient):
    """An idempotency key is scoped to a robot, not to the whole fleet.

    The key used to be the command's primary key, which is global, so the same
    key sent to two robots collided on the PK. Recovery then searched for
    ``(robot_id, idempotency_key)``, found nothing — the winner belonged to the
    *other* robot — and surfaced a 500 on a completely legitimate request. Two
    operators issuing "return-to-base-now" against different units is normal.
    """
    payload = {"command_type": "RETURN_TO_BASE", "idempotency_key": "shared-key"}

    first = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    second = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID + 1}", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200, second.json()
    # Distinct commands, because they target distinct robots.
    assert first.json()["payload"]["command_id"] != second.json()["payload"]["command_id"]


@pytest.mark.asyncio
async def test_repeated_same_state_update(client: AsyncClient):
    payload = {"command_type": "TEST", "idempotency_key": "test-key-rep"}
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    cmd_id = resp.json()["payload"]["command_id"]

    await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim")

    resp1 = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"})
    resp2 = await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"})

    assert resp1.status_code == 200
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_timeout_behavior(client: AsyncClient, db: AsyncSession):
    payload = {"command_type": "TEST", "timeout_seconds": -1}  # expires immediately
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

    # Attempt concurrent claims
    tasks = [client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim") for _ in range(5)]
    results = await asyncio.gather(*tasks)

    # Only one request should actually return the command, others should get empty list
    cmds_returned = 0
    for resp in results:
        if resp.json():
            cmds_returned += len(resp.json())

    assert cmds_returned == 1


@pytest.mark.asyncio
async def test_get_does_not_claim(client: AsyncClient, db: AsyncSession):
    """GET must be safe. The removed GET alias dispatched commands, so any
    crawler, prefetcher or retrying proxy could drain a robot's queue."""
    payload = {"command_type": "TEST", "idempotency_key": "test-key-legacy"}
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    cmd_id = resp.json()["payload"]["command_id"]

    resp = await client.get(f"/api/v1/commands/{TEST_ROBOT_ID}")
    assert resp.status_code == 405

    result = await db.execute(select(RobotCommand).where(RobotCommand.id == cmd_id))
    assert result.scalars().first().status == "PENDING"


@pytest.mark.asyncio
async def test_reused_idempotency_key_with_a_different_body_is_rejected(client: AsyncClient):
    """A replay must be the same request. Answering a different command with
    the old one would tell the operator a stop was queued when it was not."""
    key = "test-key-mismatch"
    first = await client.post(
        f"/api/v1/commands/{TEST_ROBOT_ID}",
        json={"command_type": "RETURN_TO_BASE", "idempotency_key": key},
    )
    second = await client.post(
        f"/api/v1/commands/{TEST_ROBOT_ID}",
        json={"command_type": "EMERGENCY_STOP", "idempotency_key": key},
    )

    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_late_retry_of_an_earlier_step_is_a_no_op(client: AsyncClient):
    """A robot's ACK whose response was lost, resent after it already moved
    on to EXECUTING, must succeed without rewinding the command."""
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json={"command_type": "TEST"})
    cmd_id = resp.json()["payload"]["command_id"]
    await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim")
    url = f"/api/v1/commands/{cmd_id}/status"

    await client.patch(url, json={"status": "ACKNOWLEDGED"})
    await client.patch(url, json={"status": "EXECUTING"})
    await client.patch(url, json={"status": "COMPLETED"})

    for stale in ("DISPATCHED", "ACKNOWLEDGED", "EXECUTING"):
        resp = await client.patch(url, json={"status": stale})
        assert resp.status_code == 200, (stale, resp.json())
        assert resp.json()["payload"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_unacknowledged_command_is_redelivered(client: AsyncClient, db: AsyncSession):
    """Claiming commits DISPATCHED before the response is sent. If that
    response is lost, the next claim must hand the command out again rather
    than leaving it stranded in DISPATCHED."""
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json={"command_type": "TEST"})
    cmd_id = resp.json()["payload"]["command_id"]

    first = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim")
    assert [c["id"] for c in first.json()] == [cmd_id]

    # Within the grace period the robot may still be working through it.
    soon = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim")
    assert soon.json() == []

    # Past it, with no acknowledgement, it comes back.
    await db.execute(
        update(RobotCommand)
        .where(RobotCommand.id == cmd_id)
        .values(dispatched_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    )
    await db.commit()
    later = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim")
    assert [c["id"] for c in later.json()] == [cmd_id]

    # Once acknowledged, it is the robot's and is not redelivered.
    await client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"})
    after_ack = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim")
    assert after_ack.json() == []


@pytest.mark.asyncio
async def test_concurrent_status_updates_apply_once(client: AsyncClient, db: AsyncSession):
    """Racing PATCHes on one command must not both succeed.

    Regression test for a read-modify-write race: the old implementation read
    the row, validated the transition in Python, then wrote — so two requests
    could both observe DISPATCHED, both judge their transition legal, and both
    commit. The transition is now a conditional UPDATE guarded by
    ``WHERE status = :expected``, so exactly one writer matches a row.

    The invariant is *exactly one 200*, not a particular rejection code. The
    loser can lose at either of two points, and which one depends on scheduling:

    * it reads before the winner commits, then its guarded UPDATE matches no
      row — 409;
    * it reads after the winner commits, sees a state its transition is not
      legal from, and is rejected at validation — 400.

    Both uphold the invariant. Asserting ``[200, 409]`` pinned one interleaving
    and failed roughly a third of runs on unchanged code.
    """
    payload = {"command_type": "TEST", "idempotency_key": "test-key-race"}
    resp = await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}", json=payload)
    cmd_id = resp.json()["payload"]["command_id"]

    await client.post(f"/api/v1/commands/{TEST_ROBOT_ID}/claim")  # -> DISPATCHED

    # Two different, individually-legal transitions out of DISPATCHED.
    ack, cancel = await asyncio.gather(
        client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"}),
        client.patch(f"/api/v1/commands/{cmd_id}/status", json={"status": "CANCELLED"}),
        return_exceptions=False,
    )

    statuses = sorted([ack.status_code, cancel.status_code])
    assert statuses.count(200) == 1, (
        f"expected exactly one winner, got {statuses}: {ack.json()} / {cancel.json()}"
    )
    loser_status = statuses[1] if statuses[0] == 200 else statuses[0]
    assert loser_status in (400, 409), (
        f"loser must be rejected as a conflict, got {loser_status}: {ack.json()} / {cancel.json()}"
    )

    # The database agrees with whichever request won.
    winner = ack if ack.status_code == 200 else cancel
    expected = winner.json()["payload"]["status"]

    db.expire_all()
    result = await db.execute(select(RobotCommand).where(RobotCommand.id == cmd_id))
    assert result.scalars().first().status == expected
