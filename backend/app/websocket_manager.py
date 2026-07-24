"""
WebSocket connection manager for real-time telemetry broadcast.
Uses Redis Streams to sync across multiple API instances.

Fan-out model
-------------
Each connected client gets its own bounded outbound queue and a single
long-lived sender task. Broadcasting a message is a non-blocking ``put`` onto
every client's queue — it never awaits a slow socket, and it never spawns a
task-per-message. When a client can't keep up, its queue overflows and we drop
the *oldest* buffered frame (freshest telemetry wins), which caps memory per
connection and keeps one stalled client from back-pressuring the whole fleet.
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

# Max frames buffered per client before we start dropping the oldest.
CLIENT_QUEUE_MAXSIZE = 256


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        # Per-connection outbound queue + dedicated sender task.
        self._queues: dict[WebSocket, asyncio.Queue] = {}
        self._senders: dict[WebSocket, asyncio.Task] = {}
        self.redis = get_redis()
        self.telemetry_stream = "telemetry_stream"
        self.event_stream = "event_stream"
        self._listener_task = None
        # Use lazy import for metrics to avoid circular dependencies
        self._gauge = None

    def _get_gauge(self):
        if self._gauge is None:
            from app.main import websocket_connections_active
            self._gauge = websocket_connections_active
        return self._gauge

    @property
    def connection_count(self) -> int:
        """Number of currently connected WebSocket clients."""
        return len(self.active_connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        queue: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_MAXSIZE)
        self._queues[websocket] = queue
        self._senders[websocket] = asyncio.create_task(self._sender_loop(websocket, queue))
        logger.info("WebSocket connected (total: %d)", self.connection_count)
        self._get_gauge().inc()

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection and tear down its sender."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            self._get_gauge().dec()
            logger.info("WebSocket disconnected (total: %d)", self.connection_count)

        self._queues.pop(websocket, None)
        sender = self._senders.pop(websocket, None)
        if sender is not None:
            sender.cancel()

    async def _sender_loop(self, websocket: WebSocket, queue: asyncio.Queue) -> None:
        """Drain a single client's queue, serializing sends for that socket."""
        try:
            while True:
                data = await queue.get()
                try:
                    await websocket.send_json(data)
                except (WebSocketDisconnect, RuntimeError):
                    self.disconnect(websocket)
                    return
                except Exception:
                    logger.exception("WebSocket send error")
                    self.disconnect(websocket)
                    return
        except asyncio.CancelledError:
            pass

    async def broadcast(self, data: dict, stream: str = None) -> None:
        """Publish data to Redis Streams (handles scaling/persistence)."""
        target_stream = stream or self.telemetry_stream
        try:
            # We use xadd to push the message onto a stream
            await self.redis.xadd(target_stream, {"payload": orjson.dumps(data).decode("utf-8")}, maxlen=10000)
        except Exception:
            logger.exception("Failed to publish to Redis Stream")

    async def broadcast_batch(self, data_list: list[dict], stream: str = None) -> None:
        """Publish multiple messages to Redis Streams in a single pipeline."""
        target_stream = stream or self.telemetry_stream
        if not data_list:
            return
        try:
            pipeline = self.redis.pipeline()
            for data in data_list:
                pipeline.xadd(target_stream, {"payload": orjson.dumps(data).decode("utf-8")}, maxlen=10000)
            await pipeline.execute()
        except Exception:
            logger.exception("Failed to publish batch to Redis Stream")

    async def listen_to_redis(self) -> None:
        """Background task that reads from Redis Streams and forwards to WebSockets."""
        last_ids = {self.telemetry_stream: "$", self.event_stream: "$"}
        logger.info("Subscribed to Redis Streams: %s, %s", self.telemetry_stream, self.event_stream)

        retry_delay = 2.0
        max_retry_delay = 30.0

        while True:
            try:
                # Block for 1 second waiting for messages
                streams = await self.redis.xread(last_ids, count=100, block=1000)
                if streams:
                    for stream_name_bytes, messages in streams:
                        stream_name = stream_name_bytes.decode('utf-8') if isinstance(stream_name_bytes, bytes) else stream_name_bytes
                        for message_id, message_data in messages:
                            last_ids[stream_name] = message_id
                            if "payload" in message_data:
                                try:
                                    data = orjson.loads(message_data["payload"])
                                    self._enqueue_to_all_clients(data)
                                except orjson.JSONDecodeError:
                                    logger.warning("Failed to decode Redis payload in broadcast")

                # Reset retry delay on successful read
                retry_delay = 2.0
                await asyncio.sleep(0.001)  # Yield to event loop
            except asyncio.CancelledError:
                logger.info("Unsubscribed from Redis Stream")
                break
            except Exception:
                logger.exception("Redis Stream listener error, retrying in %s seconds", retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(max_retry_delay, retry_delay * 2.0)

    def _enqueue_to_all_clients(self, data: dict) -> None:
        """Fan a message onto every client queue without blocking or spawning tasks.

        On overflow we drop the oldest buffered frame for that client and enqueue
        the new one, so a slow consumer sheds stale telemetry instead of stalling
        the broadcast loop or growing memory without bound.
        """
        for websocket in list(self.active_connections):
            queue = self._queues.get(websocket)
            if queue is None:
                continue
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()  # drop oldest
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass


manager = ConnectionManager()
