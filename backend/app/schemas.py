"""
Pydantic schemas for request validation and response serialization.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
    """Incoming event payload from a robot.

    ``timestamp`` is when the event happened on the device. Sending it makes a
    retried event idempotent; without it the server's receive time is used.
    """

    robot_id: int
    message: str = Field(max_length=512)
    timestamp: datetime | None = None


class EventResponse(BaseModel):
    """Event response payload."""

    id: int
    robot_id: int
    message: str
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class TelemetryCreate(BaseModel):
    """Incoming telemetry payload from a robot.

    ``timestamp`` is when the *device* took the reading, and is required. It
    identifies the reading (together with ``robot_id``), which is what lets a
    robot resend readings safely after a failed or unconfirmed upload, and it
    keeps store-and-forward uploads on their real timeline.
    """

    robot_id: int
    battery: float
    temperature: float
    speed: float
    timestamp: datetime
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

    model_config = ConfigDict(from_attributes=True)


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


class LoginRequest(BaseModel):
    """Credentials for POST /api/v1/auth/login."""

    username: str = Field(min_length=1, max_length=64)
    # Upper bound matches bcrypt's 72-byte input limit, rejected explicitly
    # rather than silently truncated (see app/security.hash_password).
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    """A signed access token."""

    access_token: str
    token_type: str = "bearer"
    expires_at: int
    username: str
    role: str


class PrincipalResponse(BaseModel):
    """Who the caller currently is, for GET /api/v1/auth/me."""

    username: str
    role: str
    authenticated: bool


class AuthConfigResponse(BaseModel):
    """What the dashboard needs to know before rendering anything.

    Lets the frontend decide whether to show a login screen without hardcoding
    a build-time assumption about how the backend it is talking to is configured.
    """

    auth_mode: str
    login_required: bool


class TicketResponse(BaseModel):
    """A short-lived console ticket issued to a browser client."""

    ticket: str
    # Unix seconds. The client refreshes before this rather than waiting for the
    # socket to be closed on it.
    expires_at: int
    scope: str


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    database: str
    # "connected", "disconnected", or "disabled" when MQTT_ENABLED is off.
    mqtt: str = "disabled"
    version: str = "1.0.0"


class DeviceKeyResponse(BaseModel):
    """A newly issued device key. Shown once; the server does not store it."""

    robot_id: int
    key: str
