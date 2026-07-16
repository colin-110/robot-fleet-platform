"""
Robot service — fleet status derivation.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.predictive_maintenance import summarize_robot_history
from app.repositories.telemetry_repo import TelemetryRepository

logger = logging.getLogger(__name__)


class RobotService:
    """Derives current robot status from recent telemetry history."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)

    async def get_fleet_status(self) -> list[dict]:
        """
        Return the current status summary for every robot.

        Uses optimized per-robot queries instead of loading the full
        telemetry table.
        """
        grouped = await self.repo.get_recent_per_robot(per_robot_limit=30)

        robots = []
        for robot_id in sorted(grouped):
            summary = summarize_robot_history(grouped[robot_id])
            if summary is not None:
                robots.append(summary)

        logger.debug("Fleet status computed for %d robots", len(robots))
        return robots
