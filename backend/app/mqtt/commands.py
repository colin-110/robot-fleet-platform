"""Commands pushed to robots over MQTT.

A new command is published with QoS 1 to ``fleet/<id>/commands``. The broker
acknowledges it once the message is in a subscriber's queue, which for a
robot with a persistent session means it will be delivered even if the robot
is offline right now. That acknowledgement moves the command from PENDING to
DISPATCHED.

If the robot has never subscribed, the broker has nowhere to put the message
and answers "no matching subscribers" (MQTT 5 reason code 0x10). The command
then stays PENDING and is pushed again when the robot comes online.

Robots must ignore a command id they have already seen: a command can be
pushed more than once (again on reconnect, or also claimed over HTTP).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import orjson
from paho.mqtt.reasoncodes import ReasonCode

from app.database import AsyncSessionLocal
from app.mqtt import topics
from app.repositories.command_repo import CommandRepository
from app.services.command_service import broadcast_dispatched

logger = logging.getLogger(__name__)

NO_MATCHING_SUBSCRIBERS = 0x10
ACK_TIMEOUT_SECONDS = 10.0

# Set by the app lifespan when MQTT is enabled.
publisher: CommandPublisher | None = None


def command_message(command: dict) -> bytes:
    return orjson.dumps(
        {
            "id": command["command_id"],
            "command_type": command["command_type"],
            "payload": command["payload"],
            "expires_at": command["expires_at"],
        }
    )


class CommandPublisher:
    def __init__(self, client, session_factory=AsyncSessionLocal) -> None:
        self.client = client
        self.session_factory = session_factory
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._waiting: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        client.on_publish = self._on_publish

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # paho thread. Called for everything published on this connection, not
    # only commands (watermarks, settings, relayed broadcasts), and an
    # exception here kills paho's network thread, so it must never raise.
    def _on_publish(self, client, userdata, mid, reason_code, properties) -> None:
        if self._loop is None:
            return  # nothing can be waiting before the first publish() binds the loop
        value = reason_code.value if isinstance(reason_code, ReasonCode) else int(reason_code or 0)
        self._loop.call_soon_threadsafe(self._resolve, mid, value)

    # asyncio thread. A PUBACK can never be lost to a race with publish():
    # this runs on the loop, and publish() registers its future before it
    # first yields. One that arrives after a timeout is ignored.
    def _resolve(self, mid: int, code: int) -> None:
        future = self._waiting.pop(mid, None)
        if future is not None and not future.done():
            future.set_result(code)

    async def publish(self, robot_id: int, payload: bytes) -> int | None:
        """Publish with QoS 1 and return the broker's reason code, or None
        if it did not answer in time."""
        if self._loop is None:
            self.bind(asyncio.get_running_loop())
        info = self.client.publish(topics.topic(robot_id, topics.COMMANDS), payload, qos=1)
        future = self._loop.create_future()
        self._waiting[info.mid] = future
        try:
            return await asyncio.wait_for(future, ACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self._waiting.pop(info.mid, None)
            return None

    async def push(self, command: dict) -> bool:
        """Push one command. True if it reached a robot's queue and is now
        DISPATCHED."""
        robot_id = command["robot_id"]
        code = await self.publish(robot_id, command_message(command))
        if code is None or code >= 0x80:
            logger.warning(
                "Command %s for robot %d not accepted (%s)", command["command_id"], robot_id, code
            )
            return False
        if code == NO_MATCHING_SUBSCRIBERS:
            logger.info("Robot %d has no command session yet; command stays PENDING", robot_id)
            return False

        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            dispatched = await CommandRepository(db).try_dispatch(command["command_id"], now)
        if dispatched:
            await broadcast_dispatched(
                robot_id, command["command_type"], command["command_id"], now
            )
        return True

    async def push_pending(self, robot_id: int) -> int:
        """Push every PENDING command for a robot, e.g. when it comes online."""
        async with self.session_factory() as db:
            pending = await CommandRepository(db).list_pending(robot_id)
        pushed = 0
        for record in pending:
            command = {
                "command_id": record.id,
                "robot_id": record.robot_id,
                "command_type": record.command_type,
                "payload": record.payload,
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            }
            pushed += await self.push(command)
        return pushed

    def push_soon(self, command: dict) -> None:
        self._spawn(self.push(command))

    def push_pending_soon(self, robot_id: int) -> None:
        self._spawn(self.push_pending(robot_id))

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("Pushing commands failed", exc_info=task.exception())
