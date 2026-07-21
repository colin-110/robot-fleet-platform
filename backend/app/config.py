"""
Centralized application configuration using pydantic-settings.

All settings are loaded from environment variables (or .env file).
The app will fail fast at startup if required settings are missing.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    use_redis_buffer: bool = True
    telemetry_api_key: str = "fleet-secret-key-2026"
    retention_days: int = 7

    # ── Application ─────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "INFO"

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ── Rate Limiting ───────────────────────────────────────────────
    rate_limit_per_minute: int = 600

    # ── Database Pool ───────────────────────────────────────────────
    db_pool_size: int = 20
    db_max_overflow: int = 50

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def async_database_url(self) -> str:
        """Return the database URL with the asyncpg driver."""
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings singleton.

    Raises ``ValidationError`` at startup if required env vars
    (e.g. DATABASE_URL) are missing.
    """
    return Settings()
