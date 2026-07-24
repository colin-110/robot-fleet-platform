"""
Redis-backed distributed cache.

Designed for caching computed results like fleet analytics
across all API instances.
"""

import json
import logging
from typing import Any

from app.redis_pool import get_redis

logger = logging.getLogger(__name__)


class RedisCache:
    """Simple key-value store using the shared Redis connection pool."""

    def __init__(self) -> None:
        self.client = get_redis()

    async def get(self, key: str) -> Any | None:
        """Return cached value or ``None`` if missing / expired."""
        try:
            val = await self.client.get(key)
            if val is not None:
                return json.loads(val)
        except Exception:
            logger.warning("Redis cache GET failed for key=%s", key, exc_info=True)
            return None
        return None

    async def set(self, key: str, value: Any, ttl_seconds: float = 5.0) -> None:
        """Store *value* under *key*, expiring after *ttl_seconds*."""
        try:
            await self.client.setex(key, int(ttl_seconds), json.dumps(value))
        except Exception:
            logger.warning("Redis cache SET failed for key=%s", key, exc_info=True)

    async def invalidate(self, key: str) -> None:
        """Remove a specific key from the cache."""
        try:
            await self.client.delete(key)
        except Exception:
            logger.warning("Redis cache INVALIDATE failed for key=%s", key, exc_info=True)

    async def clear(self) -> None:
        """Remove all entries (flushdb)."""
        try:
            await self.client.flushdb()
        except Exception:
            logger.warning("Redis cache CLEAR failed", exc_info=True)


# Global singleton used by services
cache = RedisCache()
