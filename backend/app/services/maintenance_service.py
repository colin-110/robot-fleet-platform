"""
Maintenance service — predictive maintenance with caching.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache
from app.ml.predictive_maintenance import build_predictive_maintenance
from app.repositories.telemetry_repo import TelemetryRepository

logger = logging.getLogger(__name__)

CACHE_KEY = "predictive_maintenance"
CACHE_TTL = 5.0  # seconds


class MaintenanceService:
    """Computes predictive maintenance scores with TTL caching."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)

    async def get_predictions(self) -> list[dict]:
        """
        Return risk predictions for all robots.

        Results are cached for ``CACHE_TTL`` seconds to avoid
        recomputing on back-to-back dashboard refreshes.
        """
        cached = await cache.get(CACHE_KEY)
        if cached is not None:
            logger.debug("Maintenance predictions served from cache")
            return cached

        grouped = await self.repo.get_recent_per_robot(per_robot_limit=30)

        predictions = []
        for robot_id in sorted(grouped):
            prediction = build_predictive_maintenance(grouped[robot_id])
            if prediction is not None:
                predictions.append(prediction)

        predictions.sort(key=lambda item: (-item["failure_risk"], item["robot_id"]))

        await cache.set(CACHE_KEY, predictions, CACHE_TTL)
        logger.debug("Maintenance predictions computed for %d robots", len(predictions))
        return predictions
