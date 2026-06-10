from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, func

from app.database import Base


class Telemetry(Base):
    __tablename__ = "telemetry"

    id = Column(Integer, primary_key=True, index=True)
    robot_id = Column(Integer, index=True)
    battery = Column(Float)
    temperature = Column(Float)
    speed = Column(Float)
    status = Column(String(32), nullable=True)
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
