"""
Telemetry data model with optimized indexes.

The composite index on (robot_id, timestamp) is the primary query pattern
used by status derivation, predictive maintenance, and analytics.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, func, JSON, UniqueConstraint
from app.database import Base

class RobotCommand(Base):
    __tablename__ = "robot_commands"

    id = Column(String(36), primary_key=True, index=True)
    robot_id = Column(Integer, index=True, nullable=False)
    command_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="PENDING")
    idempotency_key = Column(String(128), nullable=True)
    timeout_seconds = Column(Integer, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=0, nullable=False)
    
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(255), nullable=True)
    result = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_robot_commands_robot_id_desc", "robot_id", created_at.desc()),
        Index("ix_robot_commands_robot_id_status", "robot_id", "status"),
        UniqueConstraint("robot_id", "idempotency_key", name="uix_robot_id_idempotency_key"),
    )

class Telemetry(Base):
    __tablename__ = "telemetry"

    id = Column(Integer, primary_key=True, index=True)
    robot_id = Column(Integer, index=True, nullable=False)
    battery = Column(Float, nullable=False)
    temperature = Column(Float, nullable=False)
    speed = Column(Float, nullable=False)
    status = Column(String(32), nullable=True, index=True)
    mission_id = Column(String(64), nullable=True)
    mission_type = Column(String(32), nullable=True)
    mission_progress = Column(Float, nullable=True)
    mission_start_time = Column(DateTime(timezone=True), nullable=True)
    battery_health = Column(Float, nullable=True)
    motor_health = Column(Float, nullable=True)
    sensor_health = Column(Float, nullable=True)
    network_health = Column(Float, nullable=True)
    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_telemetry_robot_timestamp", "robot_id", "timestamp"),
        Index("ix_telemetry_robot_id_desc", "robot_id", timestamp.desc()),
    )

    def __repr__(self) -> str:
        return (
            f"<Telemetry id={self.id} robot_id={self.robot_id} "
            f"status={self.status!r} battery={self.battery}>"
        )
