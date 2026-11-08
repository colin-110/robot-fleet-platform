"""
Telemetry data model with optimized indexes.

The composite index on (robot_id, timestamp) is the primary query pattern
used by status derivation and analytics.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)

from app.database import Base


class Robot(Base):
    """First-class robot entity: the roster of units that *should* exist.

    This is what makes absence detectable. Deriving the fleet purely from
    recent telemetry means a robot that stops reporting eventually falls out of
    the query window and silently disappears from the dashboard - which, for a
    monitoring system, is the one failure it must never have. Registered robots
    are always listed; missing telemetry renders as OFFLINE.

    Rows are created by the worker on first sight of a robot's telemetry, so
    the roster populates itself without a provisioning step.
    """

    __tablename__ = "robots"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=True)
    model = Column(String(64), nullable=True)
    firmware_version = Column(String(32), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    registered_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    # Last time any telemetry was persisted for this robot. Lets the dashboard
    # say "silent for 3 days" for a unit long outside the telemetry window.
    last_seen = Column(DateTime(timezone=True), nullable=True, index=True)
    decommissioned_at = Column(DateTime(timezone=True), nullable=True)
    # SHA-256 of this robot's current device key (app/device_keys.py), or
    # NULL if it has none. The key itself is never stored.
    key_hash = Column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<Robot id={self.id} name={self.name!r} active={self.is_active}>"


class RobotState(Base):
    """The latest reading per robot, kept current on every write.

    Fleet status and analytics read this (one row per robot) instead of
    recomputing state from telemetry history on each request. Only a newer
    reading replaces the row, so late or resent readings never move a robot
    backwards.
    """

    __tablename__ = "robot_state"

    robot_id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    battery = Column(Float, nullable=False)
    temperature = Column(Float, nullable=False)
    speed = Column(Float, nullable=False)
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
    # Rates the robot's keyframe carried (per second), which the shared
    # predictor carries x, y and battery forward with between keyframes.
    vx = Column(Float, nullable=True)
    vy = Column(Float, nullable=True)
    battery_rate = Column(Float, nullable=True)
    # Smoothed battery drain in percent per minute; NULL until two readings
    # have been seen or after the battery starts charging.
    battery_drain_per_min = Column(Float, nullable=True)


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

    # A reading is identified by its robot and device timestamp. That pair is
    # the primary key, which makes retried uploads idempotent and is what
    # TimescaleDB requires of a hypertable's unique keys.
    robot_id = Column(Integer, primary_key=True)
    battery = Column(Float, nullable=False)
    temperature = Column(Float, nullable=False)
    speed = Column(Float, nullable=False)
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
    # Keyframe rates, per second (app/mqtt/predict.py). NULL for robots that
    # send every reading, whose values are simply held.
    vx = Column(Float, nullable=True)
    vy = Column(Float, nullable=True)
    battery_rate = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), primary_key=True)

    def __repr__(self) -> str:
        return (
            f"<Telemetry robot_id={self.robot_id} timestamp={self.timestamp} "
            f"status={self.status!r} battery={self.battery}>"
        )


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    robot_id = Column(Integer, index=True, nullable=False)
    message = Column(String(512), nullable=False)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_events_robot_id_desc", "robot_id", timestamp.desc()),
        # An event is identified by robot, device time and message, so a
        # resent or redelivered event is skipped instead of stored twice.
        UniqueConstraint("robot_id", "timestamp", "message", name="uq_events_robot_time_message"),
    )


class StoredWatermark(Base):
    """How far each robot's outbox stream is committed.

    ``seq`` is the highest sequence number such that every item of ``stream``
    up to it is stored (or was given up by the robot). It is written after a
    batch commits and is what the robot is told, so a robot deletes an item
    from its outbox only once the item is safely in the database. See
    ``app/mqtt/wire.py`` for the format and ``ingest.advance`` for the rule.
    """

    __tablename__ = "stored_watermarks"

    robot_id = Column(Integer, primary_key=True)
    stream = Column(String(64), nullable=False)
    seq = Column(BigInteger, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RobotConfig(Base):
    """A robot's settings: what the server wants and what the robot applied.

    The desired settings are also published to the robot's retained
    ``fleet/<id>/config`` topic; this row is the source the API republishes
    from. ``version`` goes up on every change, and the robot reports back the
    version it applied, so ``reported_version == version`` means in sync.
    """

    __tablename__ = "robot_configs"

    robot_id = Column(Integer, primary_key=True)
    desired = Column(JSON, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    reported = Column(JSON, nullable=True)
    reported_rejected = Column(JSON, nullable=True)
    reported_version = Column(Integer, nullable=True)
    reported_at = Column(DateTime(timezone=True), nullable=True)


class Alarm(Base):
    """A raised alarm: ACTIVE, then ACKNOWLEDGED by an operator, then CLEARED
    when its condition recovers (it can also clear straight from ACTIVE).

    At most one alarm per robot and kind is open at a time, enforced by a
    partial unique index, so a condition that persists is one alarm, not one
    per evaluation. See ``app/alarms/rules.py``.
    """

    __tablename__ = "alarms"

    id = Column(Integer, primary_key=True)
    robot_id = Column(Integer, nullable=False)
    kind = Column(String(32), nullable=False)
    severity = Column(String(16), nullable=False)
    state = Column(String(16), nullable=False, default="ACTIVE")
    message = Column(String(255), nullable=False)
    value = Column(Float, nullable=True)
    raised_at = Column(DateTime(timezone=True), nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by = Column(String(64), nullable=True)
    cleared_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_alarms_open",
            "robot_id",
            "kind",
            unique=True,
            postgresql_where=text("state <> 'CLEARED'"),
        ),
        Index("ix_alarms_raised", raised_at.desc()),
    )


class FleetSetting(Base):
    """Fleet-wide settings an operator changes at run time (the message budget)."""

    __tablename__ = "fleet_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class User(Base):
    """An operator account.

    Exists only when ``AUTH_MODE=required``. The hosted demo runs in ``open``
    mode with no accounts at all, because a login wall on a portfolio demo
    means nobody clicks past it - see ``app/security.py`` for the full
    reasoning about why this is a mode rather than a hard requirement.

    Passwords are stored as bcrypt hashes. The column is sized for bcrypt's
    60-byte output with room for a future algorithm prefix.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    # viewer | operator | admin - see app.security.ROLES.
    role = Column(String(16), nullable=False, default="viewer")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)
