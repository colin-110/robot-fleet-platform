"""
WebSocket connection manager for real-time telemetry broadcast.
Uses Redis Pub/Sub to sync across multiple API instances.
"""

import asyncio
import orjson
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from app.redis_pool import get_redis

logger = logging.getLogger(__name__)
settings = get_settings()


class ConnectionManager:
    """Manages WebSocket connections and broadcasts telemetry updates."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self.redis = get_redis()
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
            await self.redis.xadd(self.stream_name, {"payload": orjson.dumps(data).decode("utf-8")}, maxlen=10000)
        except Exception:
            logger.exception("Failed to publish to Redis Stream")

    async def listen_to_redis(self) -> None:
        """Background task that reads from Redis Streams and forwards to WebSockets."""
        last_id = "$"  # Read only new messages arriving after connection
        logger.info("Subscribed to Redis Stream: %s", self.stream_name)
        
        retry_delay = 2.0
        max_retry_delay = 30.0

        while True:
            try:
                # Block for 1 second waiting for messages
                streams = await self.redis.xread({self.stream_name: last_id}, count=100, block=1000)
                if streams:
                    for stream_name, messages in streams:
                        for message_id, message_data in messages:
                            last_id = message_id
                            if "payload" in message_data:
                                try:
                                    data = orjson.loads(message_data["payload"])
                                    await self._send_to_all_clients(data)
                                except orjson.JSONDecodeError:
                                    logger.warning("Failed to decode Redis payload in broadcast")
                
                # Reset retry delay on successful read
                retry_delay = 2.0
                await asyncio.sleep(0.001) # Yield to event loop
            except asyncio.CancelledError:
                logger.info("Unsubscribed from Redis Stream")
                break
            except Exception:
                logger.exception("Redis Stream listener error, retrying in %s seconds", retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(max_retry_delay, retry_delay * 2.0)

    async def _send_to_all_clients(self, data: dict) -> None:
        """Broadcast data to all connected clients."""
        for connection in list(self.active_connections):
            asyncio.create_task(self._send_to_client(connection, data))

    async def _send_to_client(self, websocket: WebSocket, data: dict) -> None:
        try:
            await websocket.send_json(data)
        except WebSocketDisconnect:
            self.disconnect(websocket)
        except Exception:
            logger.exception("Broadcast error")
            self.disconnect(websocket)

manager = ConnectionManager()