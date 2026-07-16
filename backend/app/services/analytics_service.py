"""
Analytics service — fleet-wide metrics with caching.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache
from app.ml.predictive_maintenance import build_fleet_analytics
from app.repositories.telemetry_repo import TelemetryRepository

logger = logging.getLogger(__name__)

CACHE_KEY = "fleet_analytics"
CACHE_TTL = 10.0  # seconds — analytics don't need sub-second freshness


class AnalyticsService:
    """Computes fleet-wide analytics with TTL caching."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)

    async def get_fleet_analytics(self) -> dict:
        """
        Return fleet analytics (health trends, distributions, mission counts).

        Results are cached for ``CACHE_TTL`` seconds.  Analytics queries
        are the most expensive since they aggregate across time, so the
        10 s cache provides meaningful load reduction.
        """
        cached = await cache.get(CACHE_KEY)
        if cached is not None:
            logger.debug("Fleet analytics served from cache")
            return cached

        rows = await self.repo.get_all_ordered(limit=5000)
        result = build_fleet_analytics(rows)

        await cache.set(CACHE_KEY, result, CACHE_TTL)
        logger.debug("Fleet analytics computed from %d rows", len(rows))
        return result
