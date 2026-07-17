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
        self.stream_name = "telemetry_stream"
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
        """Publish data to Redis Streams (handles scaling/persistence)."""
        try:
            # We use xadd to push the message onto a stream
            await self.redis.xadd(self.stream_name, {"data": json.dumps(data, default=str)}, maxlen=10000)
        except Exception:
            logger.exception("Failed to publish to Redis Stream")

    async def listen_to_redis(self) -> None:
        """Background task that reads from Redis Streams and forwards to WebSockets."""
        last_id = "$"  # Read only new messages arriving after connection
        logger.info("Subscribed to Redis Stream: %s", self.stream_name)
        
        try:
            while True:
                # Block for 1 second waiting for messages
                streams = await self.redis.xread({self.stream_name: last_id}, count=100, block=1000)
                if streams:
                    for stream_name, messages in streams:
                        for message_id, message_data in messages:
                            last_id = message_id
                            if b"data" in message_data:
                                data = json.loads(message_data[b"data"].decode("utf-8"))
                                await self._send_to_all_clients(data)
                
                await asyncio.sleep(0.001) # Yield to event loop
        except asyncio.CancelledError:
            logger.info("Unsubscribed from Redis Stream")
        except Exception:
            logger.exception("Redis Stream listener error")

    async def _send_to_all_clients(self, data: dict) -> None:
        """Helper to send data to all local active connections."""
        for connection in self.active_connections:
            asyncio.create_task(self._send_to_client(connection, data))

    async def _send_to_client(self, connection: WebSocket, data: dict) -> None:
        try:
            await connection.send_json(data)
        except WebSocketDisconnect:
            self.disconnect(connection)
        except Exception:
            logger.exception("Broadcast error")
            self.disconnect(connection)

manager = ConnectionManager()