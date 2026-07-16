"""
WebSocket connection manager for real-time telemetry broadcast.
Uses Redis Pub/Sub to sync across multiple API instances.
"""

import asyncio
import json
import logging

from fastapi import WebSocket
import redis.asyncio as aioredis
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ConnectionManager:
    """Manages WebSocket connections and broadcasts telemetry updates."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self.redis = aioredis.from_url(settings.redis_url)
        self.channel_name = "telemetry_updates"
        self._listener_task = None

    @property
    def connection_count(self) -> int:
        """Number of currently connected WebSocket clients."""
        return len(self.active_connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(
            "WebSocket connected (total: %d)", self.connection_count
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(
                "WebSocket disconnected (total: %d)", self.connection_count
            )

    async def broadcast(self, data: dict) -> None:
        """Publish data to Redis channel (instead of sending directly)."""
        try:
            await self.redis.publish(self.channel_name, json.dumps(data))
        except Exception:
            logger.exception("Failed to publish to Redis")

    async def listen_to_redis(self) -> None:
        """Background task that listens to Redis and forwards to WebSockets."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.channel_name)
        logger.info("Subscribed to Redis channel: %s", self.channel_name)
        
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    await self._send_to_all_clients(data)
        except asyncio.CancelledError:
            await pubsub.unsubscribe(self.channel_name)
            logger.info("Unsubscribed from Redis channel")
        except Exception:
            logger.exception("Redis listener error")

    async def _send_to_all_clients(self, data: dict) -> None:
        """Helper to send data to all local active connections."""
        dead_connections: list[WebSocket] = []

        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except WebSocketDisconnect:
                dead_connections.append(connection)
            except Exception:
                logger.exception("Broadcast error")
                dead_connections.append(connection)

        for connection in dead_connections:
            self.disconnect(connection)


manager = ConnectionManager()