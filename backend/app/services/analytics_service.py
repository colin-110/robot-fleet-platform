"""
Analytics service — optimized fleet-wide metrics with caching.
"""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache
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
        Uses highly optimized SQL aggregations and caches the result.
        """
        cached = await cache.get(CACHE_KEY)
        if cached is not None:
            logger.debug("Fleet analytics served from cache")
            return cached

        # Run database queries sequentially to avoid SQLAlchemy IllegalStateChangeError
        # as AsyncSession is not safe for concurrent queries on the same session.
        fleet_health_trend = await self.repo.get_fleet_health_trend(limit_minutes=20)
        mission_completion_count = await self.repo.get_mission_completions()
        recent_by_robot = await self.repo.get_recent_per_robot(per_robot_limit=1)
        
        # Calculate distributions purely from the latest state of each active robot (within 180s)
        now_time = datetime.now(timezone.utc)
        latest_rows = []
        for rows in recent_by_robot.values():
            if rows:
                latest = rows[-1]
                ts = latest.timestamp.replace(tzinfo=timezone.utc) if latest.timestamp.tzinfo is None else latest.timestamp
                if (now_time - ts).total_seconds() <= 180:
                    latest_rows.append(latest)
        
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

        await cache.set(CACHE_KEY, result, CACHE_TTL)
        logger.debug("Fleet analytics computed successfully")
        return result
