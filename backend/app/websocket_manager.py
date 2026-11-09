"""
WebSocket connection manager for real-time telemetry broadcast.

With one API process (``websocket_backend=direct``) a broadcast fans out to
this process's sockets directly. With several (``mqtt``), each broadcast is
published once to the broker's ``fleetops/live`` topic and every process fans
out what it receives there (``fan_out_relayed``), so a dashboard connected to
any instance sees every update.

Fan-out model
-------------
Each connected client gets its own bounded outbound queue and a single
long-lived sender task. Broadcasting is a non-blocking ``put`` onto every
client's queue - it never awaits a slow socket and never spawns a task per
message. When a client can't keep up its queue overflows and we drop the
*oldest* buffered frame: stale telemetry is worthless (a robot's position from
three seconds ago is noise), so the freshest data wins. This caps memory per
connection and stops one stalled client from back-pressuring the whole fleet.
"""

import asyncio
import logging

import orjson
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Max frames buffered per client before we start dropping the oldest.
CLIENT_QUEUE_MAXSIZE = 256
# The most robots one client may subscribe to or watch.
MAX_SUBSCRIBED = 10_000


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        # Per-connection outbound queue + dedicated sender task.
        self._queues: dict[WebSocket, asyncio.Queue] = {}
        self._senders: dict[WebSocket, asyncio.Task] = {}
        # Set by the lifespan in mqtt mode: publishes one relay message.
        self.relay = None
        # Which robots' telemetry each client wants (None: all of them), and
        # which it is looking at closely, which the budget allocator favours.
        self._subscriptions: dict[WebSocket, set[int] | None] = {}
        self._watching: dict[WebSocket, set[int]] = {}
        # Read by /metrics.
        self.frames_sent = 0
        self.frames_dropped = 0

    @property
    def queued_frames(self) -> int:
        """Frames waiting in every client's outbound queue."""
        return sum(q.qsize() for q in self._queues.values())

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

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection and tear down its sender."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("WebSocket disconnected (total: %d)", self.connection_count)

        self._queues.pop(websocket, None)
        self._subscriptions.pop(websocket, None)
        self._watching.pop(websocket, None)
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
                    self.frames_sent += 1
                except (WebSocketDisconnect, RuntimeError):
                    self.disconnect(websocket)
                    return
                except Exception:
                    logger.exception("WebSocket send error")
                    self.disconnect(websocket)
                    return
        except asyncio.CancelledError:
            pass

    def handle_client_message(self, websocket: WebSocket, text: str) -> None:
        """A client's control message.

        ``{"type": "subscribe", "robots": [ids] | null}``: from now on, send
        this client telemetry only for these robots (null: all). Alarms,
        events and presence always go to everyone.
        ``{"type": "watch", "robots": [ids]}``: robots the user is looking at
        closely, which get tighter tolerances under a message budget.
        """
        try:
            body = orjson.loads(text)
        except orjson.JSONDecodeError:
            return
        if not isinstance(body, dict):
            return
        robots = body.get("robots")
        ids = None
        if isinstance(robots, list):
            ids = {
                r for r in robots[:MAX_SUBSCRIBED] if isinstance(r, int) and not isinstance(r, bool)
            }
        if body.get("type") == "subscribe" and (robots is None or ids is not None):
            self._subscriptions[websocket] = ids
        elif body.get("type") == "watch" and ids is not None:
            self._watching[websocket] = ids

    def watched(self) -> set[int]:
        """Every robot some dashboard is looking at closely."""
        return set().union(*self._watching.values()) if self._watching else set()

    async def broadcast(self, data: dict) -> None:
        """Fan out one message to dashboards."""
        await self.broadcast_batch([data])

    async def broadcast_batch(self, data_list: list[dict]) -> None:
        """Fan out several messages: in-process, or as one relay message."""
        if not data_list:
            return
        if settings.websocket_backend == "mqtt":
            self._relay(data_list)
            return
        for data in data_list:
            self._fan_out(data)

    def _relay(self, data_list: list[dict]) -> None:
        """One broker message per broadcast, however many updates it holds."""
        if self.relay is None:
            # Not connected yet: serve this process's own clients at least.
            for data in data_list:
                self._fan_out(data)
            return
        try:
            self.relay(orjson.dumps(data_list))
        except Exception:
            logger.exception("Failed to relay a broadcast through the broker")

    def fan_out_relayed(self, payload: bytes) -> None:
        """Fan out a relay message received from the broker."""
        try:
            items = orjson.loads(payload)
        except orjson.JSONDecodeError:
            logger.warning("Ignoring a malformed relay message")
            return
        for data in items if isinstance(items, list) else [items]:
            self._fan_out(data)

    def _fan_out(self, data: dict) -> None:
        """Fan a message onto every client queue without blocking or spawning tasks.

        On overflow we drop the oldest buffered frame for that client and
        enqueue the new one, so a slow consumer sheds stale telemetry instead of
        stalling the broadcast loop or growing memory without bound.
        """
        # Telemetry (no "type") is filtered by subscription; the rest is not.
        robot_id = data.get("robot_id") if "type" not in data else None
        for websocket in list(self.active_connections):
            queue = self._queues.get(websocket)
            if queue is None:
                continue
            wanted = self._subscriptions.get(websocket)
            if robot_id is not None and wanted is not None and robot_id not in wanted:
                continue
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()  # drop oldest
                    self.frames_dropped += 1
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    logger.debug("Dropped frame for a slow WebSocket client")


manager = ConnectionManager()
