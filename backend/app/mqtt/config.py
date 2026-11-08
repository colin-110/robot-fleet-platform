"""Desired robot settings, published to each robot's retained config topic.

The broker keeps the latest desired settings for every robot, so a robot gets
them the moment it subscribes, online or not when they were changed. The
database is the source: every desired config is published again when the API
starts, in case the broker lost its retained messages.
"""

from __future__ import annotations

import asyncio
import logging

from app.database import AsyncSessionLocal
from app.mqtt import topics, wire
from app.repositories.config_repo import list_desired

logger = logging.getLogger(__name__)

# Set by the app lifespan when MQTT is enabled.
publisher: ConfigPublisher | None = None


class ConfigPublisher:
    def __init__(self, client, session_factory=AsyncSessionLocal) -> None:
        self.client = client
        self.session_factory = session_factory

    def publish(self, robot_id: int, version: int, values: dict) -> None:
        self.client.publish(
            topics.topic(robot_id, topics.CONFIG),
            wire.encode_config(version, values),
            qos=1,
            retain=True,
        )

    async def republish_all(self, connected: asyncio.Event) -> None:
        await connected.wait()
        async with self.session_factory() as db:
            configs = await list_desired(db)
        for robot_id, version, values in configs:
            self.publish(robot_id, version, values)
        if configs:
            logger.info("Republished desired settings for %d robots", len(configs))
