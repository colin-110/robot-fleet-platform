import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException

from app.models import RobotCommand
from app.schemas import CommandCreate, CommandStatusUpdate
from app.websocket_manager import manager

logger = logging.getLogger(__name__)

class CommandService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_command(self, robot_id: int, command: CommandCreate) -> dict:
        """Create a command with idempotency and state machine initialization."""
        cmd_id = str(command.idempotency_key) if command.idempotency_key else None
        
        import uuid
        cmd_id = cmd_id or str(uuid.uuid4())
        
        now = datetime.now(timezone.utc)
        expires_at = None
        if command.timeout_seconds:
            import datetime as dt
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
        except IntegrityError:
            await self.db.rollback()
            # If idempotency key violation, fetch the existing command
            stmt = select(RobotCommand).where(
                RobotCommand.robot_id == robot_id,
                RobotCommand.idempotency_key == command.idempotency_key
            )
            result = await self.db.execute(stmt)
            existing_cmd = result.scalars().first()
            if not existing_cmd:
                raise HTTPException(status_code=500, detail="Database conflict handling failed")
            return self._to_dict(existing_cmd)

        payload = self._to_dict(new_cmd)
        
        # Broadcast the new command
        broadcast_payload = payload.copy()
        broadcast_payload["type"] = "COMMAND_CREATED"
        broadcast_payload["timestamp"] = now.isoformat().replace("+00:00", "Z")
        await manager.broadcast(broadcast_payload)
        
        return payload

    async def update_status(self, command_id: str, update: CommandStatusUpdate) -> dict:
        """Update command status with strict state transitions."""
        stmt = select(RobotCommand).where(RobotCommand.id == command_id)
        result = await self.db.execute(stmt)
        record = result.scalars().first()
        
        if not record:
            raise HTTPException(status_code=404, detail="Command not found")

        current_status = record.status
        new_status = update.status
        now = datetime.now(timezone.utc)
        
        # Idempotency: if same status, just return (or update result/error if applicable)
        if current_status == new_status:
            return self._to_dict(record)
            
        # Terminal states check
        if current_status in ("COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"):
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot transition from terminal state {current_status}"
            )

        # Valid transitions mapping
        valid_transitions = {
            "PENDING": {"DISPATCHED", "CANCELLED", "TIMEOUT"},
            "DISPATCHED": {"ACKNOWLEDGED", "FAILED", "CANCELLED", "TIMEOUT"},
            "ACKNOWLEDGED": {"EXECUTING", "FAILED", "TIMEOUT", "COMPLETED"},
            "EXECUTING": {"COMPLETED", "FAILED", "TIMEOUT"}
        }

        if new_status not in valid_transitions.get(current_status, set()):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid transition from {current_status} to {new_status}"
            )

        record.status = new_status
        
        # Update timestamps and fields based on transition
        if new_status == "DISPATCHED":
            record.dispatched_at = now
        elif new_status == "ACKNOWLEDGED":
            record.acknowledged_at = now
        elif new_status == "EXECUTING":
            record.started_at = now
        elif new_status == "COMPLETED":
            record.completed_at = now
            record.result = update.result
        elif new_status in ("FAILED", "TIMEOUT", "CANCELLED"):
            record.completed_at = now
            record.error_code = update.error_code
            record.error_message = update.error_message
            if update.result:
                record.result = update.result

        await self.db.commit()
        await self.db.refresh(record)
        
        payload = self._to_dict(record)
        broadcast_payload = payload.copy()
        broadcast_payload["type"] = "COMMAND_UPDATE"
        broadcast_payload["timestamp"] = now.isoformat().replace("+00:00", "Z")
        await manager.broadcast(broadcast_payload)
        
        return payload

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
