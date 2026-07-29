"""The timeout scanner must not overwrite a command that finished under it.

The scan selects expired commands and then writes each one, with real time in
between. A robot can acknowledge completion inside that window, so the write
has to re-check the terminal-state condition rather than trusting what the
selection saw.

The race tests below drive that window deliberately: a *second* database
session commits the completion after the scanner's SELECT has returned but
before its UPDATE runs. A single session cannot express this — the write would
be part of the same transaction and there would be no race to lose.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models import Robot, RobotCommand
from app.worker import scan_for_timeouts
from tests.conftest import TestSessionLocal


async def _seed_robot(db, robot_id: int = 1):
    db.add(Robot(id=robot_id, last_seen=datetime.now(timezone.utc)))
    await db.commit()


async def _expired_command(db, robot_id: int = 1, status: str = "DISPATCHED") -> str:
    cmd_id = str(uuid.uuid4())
    db.add(
        RobotCommand(
            id=cmd_id,
            robot_id=robot_id,
            command_type="RETURN_TO_BASE",
            status=status,
            timeout_seconds=30,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=120),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
    )
    await db.commit()
    return cmd_id


async def _status_of(db, cmd_id: str) -> str:
    await db.rollback()  # drop any snapshot so the committed row is visible
    result = await db.execute(select(RobotCommand).where(RobotCommand.id == cmd_id))
    return result.scalars().one().status


async def _complete_in_another_session(cmd_id: str) -> None:
    """Stand in for the robot's ACK arriving on a different connection."""
    async with TestSessionLocal() as other:
        await other.execute(
            update(RobotCommand)
            .where(RobotCommand.id == cmd_id)
            .values(status="COMPLETED", completed_at=datetime.now(timezone.utc))
        )
        await other.commit()


def _race_after_select(db, monkeypatch, cmd_id: str):
    """Make `cmd_id` complete the moment the scanner's SELECT returns."""
    original_execute = db.execute
    fired = {"done": False}

    async def racing_execute(statement, *args, **kwargs):
        result = await original_execute(statement, *args, **kwargs)
        if not fired["done"]:
            fired["done"] = True
            await _complete_in_another_session(cmd_id)
        return result

    monkeypatch.setattr(db, "execute", racing_execute)


# ── Baseline behaviour ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_command_is_marked_timeout(db):
    await _seed_robot(db)
    cmd_id = await _expired_command(db)

    await scan_for_timeouts(db)

    assert await _status_of(db, cmd_id) == "TIMEOUT"


@pytest.mark.asyncio
async def test_unexpired_command_is_left_alone(db):
    await _seed_robot(db)
    cmd_id = str(uuid.uuid4())
    db.add(
        RobotCommand(
            id=cmd_id,
            robot_id=1,
            command_type="RESUME",
            status="DISPATCHED",
            timeout_seconds=300,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
        )
    )
    await db.commit()

    await scan_for_timeouts(db)

    assert await _status_of(db, cmd_id) == "DISPATCHED"


@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"])
@pytest.mark.asyncio
async def test_already_terminal_commands_are_not_selected(db, terminal):
    await _seed_robot(db)
    cmd_id = await _expired_command(db, status=terminal)

    await scan_for_timeouts(db)

    assert await _status_of(db, cmd_id) == terminal


# ── The race ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_completing_between_select_and_update_is_not_overwritten(db, monkeypatch):
    """The bug: a command the robot finished is reported as timed out.

    Without the compare-and-set in the UPDATE this assertion sees TIMEOUT.
    """
    await _seed_robot(db)
    cmd_id = await _expired_command(db)
    _race_after_select(db, monkeypatch, cmd_id)

    await scan_for_timeouts(db)

    assert await _status_of(db, cmd_id) == "COMPLETED"


@pytest.mark.asyncio
async def test_a_genuinely_stalled_command_still_times_out_during_a_race(db, monkeypatch):
    """The guard must not make the scanner give up on everything else."""
    await _seed_robot(db)
    finished_id = await _expired_command(db)
    stalled_id = await _expired_command(db)
    _race_after_select(db, monkeypatch, finished_id)

    await scan_for_timeouts(db)

    assert await _status_of(db, finished_id) == "COMPLETED"
    assert await _status_of(db, stalled_id) == "TIMEOUT"


@pytest.mark.asyncio
async def test_no_timeout_is_broadcast_for_a_command_that_completed(db, monkeypatch):
    """A broadcast cannot be retracted, so nothing may be announced that the
    database did not actually commit."""
    from app import worker

    await _seed_robot(db)
    cmd_id = await _expired_command(db)

    broadcasts = []

    async def record(payload, *_args, **_kwargs):
        broadcasts.append(payload)

    monkeypatch.setattr(worker.manager, "broadcast", record)
    _race_after_select(db, monkeypatch, cmd_id)

    await scan_for_timeouts(db)

    assert await _status_of(db, cmd_id) == "COMPLETED"
    assert broadcasts == []


@pytest.mark.asyncio
async def test_timeout_is_broadcast_once_it_is_committed(db, monkeypatch):
    from app import worker

    await _seed_robot(db)
    cmd_id = await _expired_command(db)

    broadcasts = []

    async def record(payload, *_args, **_kwargs):
        broadcasts.append(payload)

    monkeypatch.setattr(worker.manager, "broadcast", record)

    await scan_for_timeouts(db)

    assert len(broadcasts) == 1
    assert broadcasts[0]["status"] == "TIMEOUT"
    assert broadcasts[0]["command_id"] == cmd_id
