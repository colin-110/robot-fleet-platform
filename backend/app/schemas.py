from datetime import datetime

from pydantic import BaseModel


class TelemetryCreate(BaseModel):
    robot_id: int
    battery: float
    temperature: float
    speed: float
    status: str | None = None
    mission_id: str | None = None
    mission_type: str | None = None
    mission_progress: float | None = None
    mission_start_time: datetime | None = None
    battery_health: float | None = None
    motor_health: float | None = None
    sensor_health: float | None = None
    network_health: float | None = None
    x: float | None = None
    y: float | None = None
