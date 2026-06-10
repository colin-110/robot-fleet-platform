from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Float, DateTime, func
from app.database import Base

class Telemetry(Base):

    __tablename__ = "telemetry"

    id = Column(Integer, primary_key=True, index=True)

    robot_id = Column(Integer)

    battery = Column(Float)

    temperature = Column(Float)

    speed = Column(Float)

    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now()
    )
