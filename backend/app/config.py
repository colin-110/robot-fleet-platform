"""
Centralized application configuration using pydantic-settings.

All settings are loaded from environment variables (or .env file).
The app will fail fast at startup if required settings are missing.

Optimization flags
------------------
The ``opt_*`` settings each gate one performance optimization. They all default
to ``True`` (optimized). Turning one off restores the naive implementation it
replaced, which is what ``scripts/benchmark_matrix.py`` uses to measure the
before/after delta for that single change in isolation.
"""

import logging
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ────────────────────────────────────────────────────
    database_url: str

    # ── Redis ───────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Auth ────────────────────────────────────────────────────────
    # No default: a missing key must fail loudly at startup rather than
    # silently falling back to a value that is public in this repo.
    telemetry_api_key: str

    # Lifetime of a browser ticket (app/tickets.py). Long enough to survive a
    # reconnect storm, short enough that a scraped ticket is quickly worthless.
    # The dashboard fetches a fresh one whenever its socket reconnects.
    ticket_ttl_seconds: int = 300

    # ── Authentication ──────────────────────────────────────────────
    # "open"     — no accounts; the console is public (the hosted demo).
    # "required" — every console action needs a JWT from /api/v1/auth/login.
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

    retention_days: int = 1

    # ── Application ─────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"
    # "text" for a terminal, "json" for a log aggregator. Unset means text in
    # development and json in production — see resolved_log_format.
    log_format_setting: str | None = Field(default=None, alias="LOG_FORMAT")
    # Emit one structured line per HTTP request. Uvicorn's own access log is
    # disabled in production, so without this a deployed request leaves no
    # trace at all — which is how a command dispatch became unverifiable.
    access_log: bool = True

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ── Realtime fan-out ────────────────────────────────────────────
    # "redis" publishes every broadcast via XADD and a background XREAD loop
    # consumes it — correct when multiple API instances each need every
    # message, which is the whole point of Redis Streams here. A single
    # instance (Render's free tier) has no other instance to synchronize
    # with, and broadcasting on every telemetry reading from a continuously
    # ingesting fleet turns into real, metered Redis command volume for no
    # benefit. "direct" calls the same in-process fan-out immediately instead
    # of round-tripping through Redis — same real-time behavior for exactly
    # one instance, lower latency, and it's the other half (with
    # rate_limit_backend) of what actually exhausted a free Upstash budget.
    websocket_backend: str = "redis"

    # ── Rate Limiting ───────────────────────────────────────────────
    # Same one-instance-vs-many reasoning as websocket_backend above: "redis"
    # shares one counter across instances via a per-request pipeline (3
    # writes + 1 read); "memory" is a plain in-process sliding window,
    # correct for exactly one instance and free. The per-IP keyspace this app
    # ever sees (a fleet's own IP, a handful of browsers) is small enough
    # that it never needs pruning.
    rate_limit_backend: str = "redis"
    rate_limit_per_minute: int = 600
    # Separate, tighter budget for what a browser can reach without the master
    # key: minting console tickets and dispatching commands. Both are public on
    # the hosted demo, so they need a ceiling the fleet's ingest budget would
    # not give them.
    console_rate_limit_per_minute: int = 60
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
    # reading inside this window still appear — sourced from the robot registry
    # and rendered OFFLINE — rather than disappearing from the fleet entirely.
    fleet_window_minutes: int = 15

    # Rows of history per robot used to derive status and extrapolate battery
    # drain rate.
    fleet_history_per_robot: int = 30

    # ── Ingest ──────────────────────────────────────────────────────

    # Devices may report their own measurement time, which is what makes
    # store-and-forward possible: a robot that buffers readings through a
    # network outage can upload them with their real timestamps. A wrong device
    # clock would otherwise poison time-bucketed analytics, so a reading dated
    # more than this many seconds in the future is clamped to server time.
    max_clock_skew_seconds: int = 300

    # Max entries retained in the telemetry Redis Stream. Redis trims the
    # oldest beyond this — including entries a consumer group has not yet
    # acknowledged — so this is the worker's catch-up headroom, not an
    # unbounded durable log. The worker exports stream length and pending
    # count as metrics so falling behind is visible rather than silent.
    telemetry_stream_maxlen: int = 100_000

    # ── Access control ──────────────────────────────────────────────

    # Whether the read-only endpoints (fleet status, analytics, events) require
    # an API key. Defaults to public so the hosted demo works without shipping
    # a key to every browser; set true for any deployment with real data.
    require_auth_for_reads: bool = False

    # ── Optimization flags (all default ON; flip OFF to benchmark) ──

    # Ingest path: XADD to a Redis Stream and return immediately, versus a
    # synchronous INSERT + COMMIT on the request path.
    opt_redis_buffer: bool = True

    # Read path: Redis read-through cache in front of fleet-status and
    # analytics aggregation, versus recomputing on every request.
    opt_read_cache: bool = True

    # Middleware: pure ASGI middleware versus Starlette BaseHTTPMiddleware
    # (which wraps every request in an extra task + anyio memory streams).
    opt_asgi_middleware: bool = True

    # WebSocket fan-out: bounded per-client queue drained by one dedicated
    # sender task, versus spawning a send task per message per client.
    opt_bounded_fanout: bool = True

    # Worker: single multi-row INSERT per batch, versus one INSERT per row.
    opt_batch_insert: bool = True

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
        ``Access-Control-Allow-Credentials: true`` — browsers reject the
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
        defaulting those to a public console is exactly the mistake this
        setting exists to prevent. The demo sets it explicitly.
        """
        if self.auth_mode:
            return self.auth_mode.lower()
        return "required" if self.is_production else "open"

    @property
    def auth_required(self) -> bool:
        return self.resolved_auth_mode == "required"

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
                # console is allowed — it is how the hosted demo runs — but it
                # must be a decision someone typed, not a default they inherited.
                logger.warning(
                    "APP_ENV=production with AUTH_MODE=open: the operator console "
                    "is public. Anyone who can reach it can dispatch commands."
                )
            if self.jwt_secret is not None and len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters in production.")

        if self.resolved_auth_mode not in ("open", "required"):
            raise ValueError(f"AUTH_MODE must be 'open' or 'required', got {self.auth_mode!r}.")
        return self

    def optimization_flags(self) -> dict[str, bool]:
        """Current state of every ``opt_*`` flag, for /health and benchmarks."""
        # type(self).model_fields, not self.model_fields: Pydantic 2.11
        # deprecated instance access, and this runs on every /health scrape.
        return {
            name: getattr(self, name)
            for name in sorted(type(self).model_fields)
            if name.startswith("opt_")
        }


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings singleton.

    Raises ``ValidationError`` at startup if required env vars
    (``DATABASE_URL``, ``TELEMETRY_API_KEY``) are missing.
    """
    return Settings()
