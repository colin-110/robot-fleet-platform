"""
Pydantic schemas for request validation and response serialization.
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ── Request Schemas ─────────────────────────────────────────────────


class CommandCreate(BaseModel):
    """Command payload sent from frontend to robot."""
    command_type: str
    payload: dict | None = None
    timeout_seconds: int | None = None
    idempotency_key: str | None = None


class CommandStatusUpdate(BaseModel):
    """Status update for an existing command."""
    status: str
    error_code: str | None = None
    error_message: str | None = None
    result: dict | None = None


class EventCreate(BaseModel):
    """Incoming event payload from a robot."""
    robot_id: int
    message: str


class EventResponse(BaseModel):
    """Event response payload."""
    id: int
    robot_id: int
    message: str
    timestamp: datetime

    class Config:
        from_attributes = True



class TelemetryCreate(BaseModel):
    """Incoming telemetry payload from the simulator."""

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


class TelemetryResponse(BaseModel):
    """Telemetry response payload."""
    id: int
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
    timestamp: datetime

    class Config:
        from_attributes = True


# ── Response Schemas ────────────────────────────────────────────────


class TelemetryAck(BaseModel):
    """Acknowledgement returned after telemetry ingestion."""

    message: str
    id: int


class RobotStatusResponse(BaseModel):
    """Current status summary for a single robot."""

    robot_id: int
    battery: float
    temperature: float
    speed: float
    status: str
    mission_id: str | None = None
    mission_type: str | None = None
    mission_progress: float | None = None
    mission_start_time: str | None = None
    last_seen: str | None = None
    runtime_remaining_minutes: float | None = None
    x: float = 0.0
    y: float = 0.0
    battery_health: float = 100.0
    motor_health: float = 100.0
    sensor_health: float = 100.0
    network_health: float = 100.0


class DistributionBucket(BaseModel):
    """A single bucket in a distribution chart."""

    range: str
    count: int


class StatusBreakdownItem(BaseModel):
    """A single status category in the robot status breakdown."""

    status: str
    count: int


class MissionCompletionItem(BaseModel):
    """Completed mission count per mission type."""

    mission_type: str
    count: int


class HealthTrendPoint(BaseModel):
    """A single point in the fleet health trend time series."""

    timestamp: str | None
    health_score: float


class FleetAnalyticsResponse(BaseModel):
    """Fleet-wide analytics payload."""

    fleet_health_trend: list[HealthTrendPoint] = Field(default_factory=list)
    battery_distribution: list[DistributionBucket] = Field(default_factory=list)
    temperature_distribution: list[DistributionBucket] = Field(default_factory=list)
    mission_completion_count: list[MissionCompletionItem] = Field(default_factory=list)
    robot_status_breakdown: list[StatusBreakdownItem] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    database: str
    version: str = "1.0.0"
