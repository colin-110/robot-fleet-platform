"""
Analytics service - fleet-wide distributions, trends and mission counts.
"""

import logging
from collections import Counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.robot_repo import RobotRepository
from app.repositories.telemetry_repo import TelemetryRepository

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Computes fleet-wide analytics."""

    def __init__(self, db: AsyncSession) -> None:
        self.repo = TelemetryRepository(db)
        self.robots = RobotRepository(db)

    async def get_fleet_analytics(self) -> dict:
        """
        Return fleet analytics (health trends, distributions, mission counts).
        Distributions come from robot_state (one row per robot); the trend
        and mission counts are SQL aggregations over recent telemetry.
        """
        # Run sequentially: an AsyncSession cannot run concurrent queries.
        fleet_health_trend = await self.repo.get_fleet_health_trend(limit_minutes=20)
        mission_completion_count = await self.repo.get_mission_completions()
        # Distributions describe robots that reported in the last three minutes.
        latest_rows = await self.robots.recent_states(within_seconds=180)

        battery_ranges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
        temperature_ranges = [(0, 40), (40, 55), (55, 70), (70, 85), (85, 200)]

        battery_distribution = []
        for lower, upper in battery_ranges:
            label = f"{lower}-{upper - 1 if upper < 101 else 100}%"
            count = sum(lower <= float(row.battery) < upper for row in latest_rows)
            battery_distribution.append({"range": label, "count": count})

        temperature_distribution = []
        for lower, upper in temperature_ranges:
            suffix = f"{upper - 1}C" if upper < 200 else "95C+"
            label = f"{lower}-{suffix}"
            count = sum(lower <= float(row.temperature) < upper for row in latest_rows)
            temperature_distribution.append({"range": label, "count": count})

        status_counter = Counter()
        for row in latest_rows:
            status = row.status.upper() if row.status else "UNKNOWN"
            status_counter[status] += 1

        robot_status_breakdown = [
            {"status": status, "count": status_counter.get(status, 0)}
            for status in ["ACTIVE", "LOW POWER", "OVERHEATING", "OFFLINE", "CHARGING", "DEAD"]
        ]

        result = {
            "fleet_health_trend": fleet_health_trend,
            "battery_distribution": battery_distribution,
            "temperature_distribution": temperature_distribution,
            "mission_completion_count": mission_completion_count,
            "robot_status_breakdown": robot_status_breakdown,
        }

        logger.debug("Fleet analytics computed successfully")
        return result
