"""
Redis-backed distributed cache.

Designed for caching computed results like analytics and
predictive maintenance scores across all API instances.
"""

import json
from typing import Any
import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()

class RedisCache:
    """Simple key-value store using Redis."""

    def __init__(self) -> None:
        self.client = redis.Redis.from_url(settings.redis_url)

    async def get(self, key: str) -> Any | None:
        """Return cached value or ``None`` if missing / expired."""
        try:
            val = await self.client.get(key)
            if val is not None:
                return json.loads(val)
        except Exception:
            return None
        return None

    async def set(self, key: str, value: Any, ttl_seconds: float = 5.0) -> None:
        """Store *value* under *key*, expiring after *ttl_seconds*."""
        try:
            await self.client.setex(key, int(ttl_seconds), json.dumps(value))
        except Exception:
            pass

    async def invalidate(self, key: str) -> None:
        """Remove a specific key from the cache."""
        try:
            await self.client.delete(key)
        except Exception:
            pass

    async def clear(self) -> None:
        """Remove all entries (flushdb)."""
        try:
            await self.client.flushdb()
        except Exception:
            pass

# Global singleton used by services
cache = RedisCache()
