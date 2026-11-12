"""
Centralized application configuration using pydantic-settings.

All settings are loaded from environment variables (or .env file).
The app will fail fast at startup if required settings are missing.
"""

import logging
import os
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Marks every secret in .env.example; the app refuses to start with one.
PLACEHOLDER = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # ENV_FILE="" reads no file at all: the test suite sets it, so tests
        # mean the same on a developer machine (with its own backend/.env) as
        # in CI (with none).
        env_file=os.environ.get("ENV_FILE", ".env") or None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ────────────────────────────────────────────────────
    database_url: str

    # ── Auth ────────────────────────────────────────────────────────
    # No default: a missing key must fail loudly at startup rather than
    # silently falling back to a value that is public in this repo.
    telemetry_api_key: str

    # Per-robot device keys (app/device_keys.py). While this is true the
    # master key still works on robot endpoints; set it false once every
    # robot has its own key.
    allow_shared_ingest_key: bool = True
    # How often each instance reloads key hashes so revocations propagate.
    # Only runs with websocket_backend=mqtt (multiple instances).
    device_key_refresh_seconds: int = 60

    # Lifetime of a browser ticket (app/tickets.py). Long enough to survive a
    # reconnect storm, short enough that a scraped ticket is quickly worthless.
    # The dashboard fetches a fresh one whenever its socket reconnects.
    ticket_ttl_seconds: int = 300

    # ── Authentication ──────────────────────────────────────────────
    # "open"     - no accounts; the console is public (the hosted demo).
    # "required" - every console action needs a JWT from /api/v1/auth/login.
    # Left unset, production defaults to "required"; see the validator below.
    auth_mode: str | None = None

    # Signing key for access tokens. Derived from telemetry_api_key when unset,
    # so there is no second secret to manage and rotating the master key
    # invalidates outstanding tokens.
    jwt_secret: str | None = None
    jwt_ttl_seconds: int = 3600

    # Optional bootstrap account, created at startup if the users table is
    # empty. Without it, a fresh deployment in required mode has no way in.
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None

    # ── Application ─────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    # "text" for a terminal, "json" for a log aggregator. Unset means text in
    # development and json in production - see resolved_log_format.
    log_format_setting: str | None = Field(default=None, alias="LOG_FORMAT")
    # One structured log line per HTTP request. Uvicorn's access log is off in
    # production, so this is the only request trace there.
    access_log: bool = True

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ── MQTT ────────────────────────────────────────────────────────
    # The API's own broker account. It logs in with the master key and is a
    # superuser; robots log in as robot-<id> with their device key.
    mqtt_service_username: str = "fleetops"
    # Run the ingest consumer in this process. Off by default so a plain
    # HTTP setup (and the test suite) needs no broker.
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    # Unset (the default): the ingest consumer uses plain subscriptions.
    # Set it to a group name to use shared subscriptions, so several API
    # processes split the robots' messages. Only do that knowingly: Mosquitto
    # does not restore shared subscriptions after a broker restart, so messages
    # published before the consumers resubscribe are dropped. A plain
    # persistent subscription keeps queueing across the restart.
    mqtt_ingest_group: str | None = None
    # A batch is written when it reaches this many messages or this age.
    mqtt_batch_size: int = 200
    mqtt_batch_wait_ms: int = 250

    # ── Realtime fan-out ────────────────────────────────────────────
    # "direct" fans out in-process: correct for a single instance and the
    # cheapest. "mqtt" relays every broadcast through the broker so several
    # API instances all see every update (needs MQTT_ENABLED); it also marks
    # the deployment as multi-instance for the device-key cache.
    websocket_backend: str = "direct"

    # ── Rate Limiting ───────────────────────────────────────────────
    # In-process sliding windows (app/middleware.py), per client IP.
    rate_limit_per_minute: int = 600
    # Separate, tighter budget for what a browser can reach without the master
    # key: minting console tickets and dispatching commands. Both are public on
    # the hosted demo, so they need a ceiling the fleet's ingest budget would
    # not give them.
    console_rate_limit_per_minute: int = 60
    # Sign-in attempts per client IP. Passwords are bcrypt-hashed, which slows
    # each guess but does not limit how many are made.
    login_rate_limit_per_minute: int = 10
    # Number of reverse proxies in front of the app (nginx, CloudFront, ALB).
    # The rate limiter takes the Nth-from-last X-Forwarded-For entry so a client
    # cannot spoof its own IP by injecting the header. 0 = no proxy, trust the
    # socket peer address.
    trusted_proxy_count: int = 0

    # ── Database Pool ───────────────────────────────────────────────
    db_pool_size: int = 20
    db_max_overflow: int = 50

    # ── Fleet state ─────────────────────────────────────────────────

    # A robot is reported OFFLINE once this many seconds have passed since its
    # last reading. Single source of truth: previously three different
    # thresholds (60/180/300) were checked in two places, two of which were
    # unreachable.
    offline_after_seconds: int = 60

    # How far back the fleet-status query scans for telemetry. Robots with no
    # reading inside this window still appear - sourced from the robot registry
    # and rendered OFFLINE - rather than disappearing from the fleet entirely.
    fleet_window_minutes: int = 15

    # ── Ingest ──────────────────────────────────────────────────────

    # Devices may report their own measurement time, which is what makes
    # store-and-forward possible: a robot that buffers readings through a
    # network outage can upload them with their real timestamps. A wrong device
    # clock would otherwise poison time-bucketed analytics, so a reading dated
    # more than this many seconds in the future is clamped to server time.
    max_clock_skew_seconds: int = 300

    # ── Alarms ──────────────────────────────────────────────────────
    # How often every robot is checked against the alarm rules (in memory).
    alarm_interval_seconds: float = 2.0

    # ── Line segments (app/storage/lines_job.py) ────────────────────
    # Each field's segments stay within the robot's own tolerance times this,
    # so the total error against what the robot measured is at most (1 +
    # scale) times its tolerance. Measured on the simulator's fleet: 1 stores
    # 62% fewer numbers than the keyframe rows, 2 stores 74%, 3 stores 80%.
    line_tolerance_scale: float = 1.0
    # Raw keyframes older than this are dropped (app/storage/retention.py),
    # but only for hours whose line segments are done and verified, and after
    # copying the keyframes around every alarm to telemetry_incidents (kept a
    # year). History beyond it is served from the segments.
    raw_retention_days: float = 30.0
    # How far either side of an alarm counts as part of the incident.
    incident_margin_minutes: float = 10.0
    # POST every alarm change here, signed with the secret (app/alarms/webhook.py).
    alarm_webhook_url: str | None = None
    alarm_webhook_secret: str | None = None
    # POST every alarm change here, signed with the secret (app/alarms/webhook.py).
    alarm_webhook_url: str | None = None
    alarm_webhook_secret: str | None = None

    # ── Access control ──────────────────────────────────────────────

    # Whether the read-only endpoints (fleet status, positions, telemetry,
    # analytics, events, alarms) need a signed-in user or the master key.
    # Unset follows AUTH_MODE: required sign-in protects the data too, not
    # only the commands. Set false explicitly for a public read-only demo.
    require_auth_for_reads: bool | None = None

    # ── Derived / validated ─────────────────────────────────────────

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        """Credentials cannot be combined with a wildcard origin.

        The CORS spec forbids ``Access-Control-Allow-Origin: *`` together with
        ``Access-Control-Allow-Credentials: true`` - browsers reject the
        response. Rather than emit an invalid header pair we drop credentials
        whenever the origin list is a wildcard.
        """
        return self.cors_origin_list != ["*"]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def log_format(self) -> str:
        """``text`` or ``json``. Production defaults to json."""
        if self.log_format_setting:
            return self.log_format_setting.lower()
        return "json" if self.is_production else "text"

    @property
    def resolved_auth_mode(self) -> str:
        """``open`` or ``required``, with production defaulting to required.

        Unset means "use the safe default for this environment" rather than
        "open": a production deployment carries real fleet positions, and
        should not default to a public console. The demo sets it explicitly.
        """
        if self.auth_mode:
            return self.auth_mode.lower()
        return "required" if self.is_production else "open"

    @property
    def auth_required(self) -> bool:
        return self.resolved_auth_mode == "required"

    @property
    def reads_require_auth(self) -> bool:
        if self.require_auth_for_reads is not None:
            return self.require_auth_for_reads
        return self.auth_required

    @property
    def async_database_url(self) -> str:
        """Return the database URL with the asyncpg driver."""
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url

    @model_validator(mode="after")
    def _guard_production(self) -> "Settings":
        """Refuse the most dangerous misconfigurations in production."""
        if self.is_production:
            if self.cors_origin_list == ["*"]:
                raise ValueError(
                    "CORS_ORIGINS='*' is not allowed when APP_ENV=production. "
                    "Set it to the explicit dashboard origin(s)."
                )
            if len(self.telemetry_api_key) < 16:
                raise ValueError("TELEMETRY_API_KEY must be at least 16 characters in production.")
            if not self.auth_required:
                # Explicitly opting a production deployment into a public
                # console is allowed - it is how the hosted demo runs - but it
                # must be a decision someone typed, not a default they inherited.
                logger.warning(
                    "APP_ENV=production with AUTH_MODE=open: the operator console "
                    "is public. Anyone who can reach it can dispatch commands."
                )
            if self.jwt_secret is not None and len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters in production.")

        # The example file's placeholders are public, so a copy that was never
        # filled in would run with credentials anyone can read. In every
        # environment, not only production: a local stack is reachable too.
        placeholders = [
            name
            for name, value in (
                ("TELEMETRY_API_KEY", self.telemetry_api_key),
                ("DATABASE_URL", self.database_url),
                ("BOOTSTRAP_ADMIN_PASSWORD", self.bootstrap_admin_password),
                ("JWT_SECRET", self.jwt_secret),
            )
            if value and PLACEHOLDER in value
        ]
        if placeholders:
            raise ValueError(
                f"{', '.join(placeholders)} still hold the example's placeholder "
                f"('{PLACEHOLDER}...'). Run `python3 backend/make_env.py` to fill "
                "them with random values."
            )

        if self.websocket_backend not in ("direct", "mqtt"):
            raise ValueError(
                f"WEBSOCKET_BACKEND must be 'direct' or 'mqtt', got {self.websocket_backend!r}."
            )
        if self.resolved_auth_mode not in ("open", "required"):
            raise ValueError(f"AUTH_MODE must be 'open' or 'required', got {self.auth_mode!r}.")
        return self


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings singleton.

    Raises ``ValidationError`` at startup if required env vars
    (``DATABASE_URL``, ``TELEMETRY_API_KEY``) are missing.
    """
    return Settings()
