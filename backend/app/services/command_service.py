"""
Command service — creation, idempotency, and the status state machine.
"""

import datetime as dt
import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models import RobotCommand
from app.schemas import CommandCreate, CommandStatusUpdate
from app.websocket_manager import manager

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"})

# Explicit state machine. Anything not listed is rejected.
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"DISPATCHED", "CANCELLED", "TIMEOUT"}),
    "DISPATCHED": frozenset({"ACKNOWLEDGED", "FAILED", "CANCELLED", "TIMEOUT"}),
    "ACKNOWLEDGED": frozenset({"EXECUTING", "FAILED", "TIMEOUT", "COMPLETED"}),
    "EXECUTING": frozenset({"COMPLETED", "FAILED", "TIMEOUT"}),
}


class CommandService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_command(self, robot_id: int, command: CommandCreate) -> dict:
        """Create a command with idempotency and state machine initialization."""
        # A fresh id every time. Using the idempotency key as the primary key
        # conflated two scopes: the key is unique per (robot_id, key) via its
        # own constraint, but the PK is global — so the same key sent to two
        # different robots collided on the PK. Recovery then looked up
        # (robot_id, idempotency_key), found nothing (the winner belonged to
        # the other robot), and raised a 500 on a legitimate request.
        cmd_id = str(uuid.uuid4())

        now = datetime.now(timezone.utc)
        expires_at = None
        if command.timeout_seconds:
            expires_at = now + dt.timedelta(seconds=command.timeout_seconds)

        new_cmd = RobotCommand(
            id=cmd_id,
            robot_id=robot_id,
            command_type=command.command_type,
            payload=command.payload,
            status="PENDING",
            idempotency_key=command.idempotency_key,
            timeout_seconds=command.timeout_seconds,
            expires_at=expires_at,
            created_at=now,
        )
        self.db.add(new_cmd)

        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            # Idempotency key collision — return the command that won the race.
            stmt = select(RobotCommand).where(
                RobotCommand.robot_id == robot_id,
                RobotCommand.idempotency_key == command.idempotency_key,
            )
            result = await self.db.execute(stmt)
            existing_cmd = result.scalars().first()
            if not existing_cmd:
                raise HTTPException(
                    status_code=500, detail="Database conflict handling failed"
                ) from exc
            return self._to_dict(existing_cmd)

        payload = self._to_dict(new_cmd)

        broadcast_payload = payload.copy()
        broadcast_payload["type"] = "COMMAND_CREATED"
        broadcast_payload["timestamp"] = now.isoformat().replace("+00:00", "Z")
        # event_stream, not the default telemetry_stream: that one is the
        # bounded ingest buffer the worker drains, and command traffic sharing
        # it eats the worker's catch-up headroom.
        await manager.broadcast(broadcast_payload, stream=manager.event_stream)

        return payload

    async def update_status(self, command_id: str, update_data: CommandStatusUpdate) -> dict:
        """Advance a command through the state machine.

        The transition is applied as a single conditional UPDATE guarded by
        ``WHERE status = :expected``. Reading the row, validating in Python,
        then writing would be a read-modify-write race: two concurrent PATCHes
        can both read ``ACKNOWLEDGED``, both decide their transition is legal,
        and both commit — losing one update and letting a command reach a
        terminal state twice. Letting the database do the compare-and-set means
        exactly one of the two writers matches a row; the loser re-reads and
        gets a 409.

        This mirrors how ``CommandRepository.try_dispatch`` claims a PENDING
        command, and holds regardless of transaction isolation level.
        """
        record = await self._get_or_404(command_id)
        current_status = record.status
        new_status = update_data.status
        now = datetime.now(timezone.utc)

        # Replaying the same status is a no-op, not an error — makes retries safe.
        if current_status == new_status:
            return self._to_dict(record)

        if current_status in TERMINAL_STATES:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from terminal state {current_status}",
            )

        if new_status not in VALID_TRANSITIONS.get(current_status, frozenset()):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid transition from {current_status} to {new_status}",
            )

        values = self._transition_values(new_status, update_data, now)
        stmt = (
            update(RobotCommand)
            .where(
                RobotCommand.id == command_id,
                RobotCommand.status == current_status,  # compare-and-set
            )
            .values(**values)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()

        # The session is configured with expire_on_commit=False, so the identity
        # map still holds the pre-UPDATE attribute values. Expire it or every
        # read below returns the stale row we started from.
        self.db.expire_all()

        if result.rowcount == 0:
            # Another writer moved the command between our read and our write.
            current = await self._get_or_404(command_id)
            if current.status == new_status:
                # They applied the same transition we wanted — treat as success.
                return self._to_dict(current)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Command was concurrently updated to {current.status}; "
                    f"transition to {new_status} no longer applies"
                ),
            )

        record = await self._get_or_404(command_id)
        payload = self._to_dict(record)

        broadcast_payload = payload.copy()
        broadcast_payload["type"] = "COMMAND_UPDATE"
        broadcast_payload["timestamp"] = now.isoformat().replace("+00:00", "Z")
        await manager.broadcast(broadcast_payload, stream=manager.event_stream)

        return payload

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _transition_values(
        new_status: str, update_data: CommandStatusUpdate, now: datetime
    ) -> dict:
        """Columns to write for a given target state."""
        values: dict = {"status": new_status}

        if new_status == "DISPATCHED":
            values["dispatched_at"] = now
        elif new_status == "ACKNOWLEDGED":
            values["acknowledged_at"] = now
        elif new_status == "EXECUTING":
            values["started_at"] = now
        elif new_status == "COMPLETED":
            values["completed_at"] = now
            values["result"] = update_data.result
        elif new_status in TERMINAL_STATES:
            values["completed_at"] = now
            values["error_code"] = update_data.error_code
            values["error_message"] = update_data.error_message
            if update_data.result:
                values["result"] = update_data.result

        return values

    async def _get_or_404(self, command_id: str) -> RobotCommand:
        stmt = select(RobotCommand).where(RobotCommand.id == command_id)
        result = await self.db.execute(stmt)
        record = result.scalars().first()
        if not record:
            raise HTTPException(status_code=404, detail="Command not found")
        return record

    def _to_dict(self, record: RobotCommand) -> dict:
        return {
            "command_id": record.id,
            "robot_id": record.robot_id,
            "command_type": record.command_type,
            "payload": record.payload,
            "status": record.status,
            "idempotency_key": record.idempotency_key,
            "timeout_seconds": record.timeout_seconds,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "error_code": record.error_code,
            "error_message": record.error_message,
            "result": record.result,
        }
