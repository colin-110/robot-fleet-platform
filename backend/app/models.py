from sqlalchemy import Column, Integer, Float
from app.database import Base

class Telemetry(Base):

    __tablename__ = "telemetry"

    id = Column(Integer, primary_key=True, index=True)

    robot_id = Column(Integer)

    battery = Column(Float)

    temperature = Column(Float)

    speed = Column(Float)