"""
Centralized Redis connection pool.

All modules that need Redis should import ``get_redis`` instead of
creating their own connections. This ensures a single shared pool
and consistent configuration.
"""

import redis.asyncio as aioredis

from app.config import get_settings

settings = get_settings()

# Shared connection pool used by all Redis consumers
_pool = aioredis.ConnectionPool.from_url(
    settings.redis_url,
    max_connections=20,
    decode_responses=False,
)


def get_redis() -> aioredis.Redis:
    """Return a Redis client backed by the shared connection pool."""
    return aioredis.Redis(connection_pool=_pool)
