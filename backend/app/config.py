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

from pydantic import model_validator
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

    retention_days: int = 1

    # ── Application ─────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ── Rate Limiting ───────────────────────────────────────────────
    rate_limit_per_minute: int = 600
    # Number of reverse proxies in front of the app (nginx, CloudFront, ALB).
    # The rate limiter takes the Nth-from-last X-Forwarded-For entry so a client
    # cannot spoof its own IP by injecting the header. 0 = no proxy, trust the
    # socket peer address.
    trusted_proxy_count: int = 0

    # ── Database Pool ───────────────────────────────────────────────
    db_pool_size: int = 20
    db_max_overflow: int = 50

    # ── Optimization flags (all default ON; flip OFF to benchmark) ──

    # Ingest path: XADD to a Redis Stream and return immediately, versus a
    # synchronous INSERT + COMMIT on the request path.
    opt_redis_buffer: bool = True

    # Read path: Redis read-through cache in front of fleet-status and
    # analytics aggregation, versus recomputing on every request.
    opt_read_cache: bool = True

    # Serialization: orjson response class versus stdlib json.
    opt_orjson: bool = True

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
        return self

    def optimization_flags(self) -> dict[str, bool]:
        """Current state of every ``opt_*`` flag, for /health and benchmarks."""
        return {
            name: getattr(self, name)
            for name in sorted(self.model_fields)
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
