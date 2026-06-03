from pydantic import BaseModel

class TelemetryCreate(BaseModel):

    robot_id: int

    battery: float

    temperature: float

    speed: float