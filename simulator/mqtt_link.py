"""One simulated robot's MQTT connection.

Each robot logs in as ``robot-<id>`` with its own device key, on a persistent
session (so commands sent while it is offline are kept for it), and:

- registers a retained ``offline`` presence message as its last will, which
  the broker publishes the moment the connection drops;
- publishes a retained ``online`` presence message on every connect, tagged
  with a fresh connection id so the server can ignore the will of an older
  connection that arrives late;
- subscribes to ``fleet/<id>/commands`` and hands commands to the robot loop.

paho runs each connection's network loop in its own thread. Unacknowledged
QoS 1 messages stay in paho's queue and are resent after a reconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

logger = logging.getLogger(__name__)

SESSION_EXPIRY_SECONDS = 7 * 24 * 3600
# Readings kept in memory while the broker is unreachable, per robot.
MAX_QUEUED = 5000


class RobotLink:
    def __init__(self, robot_id: int, key: str, host: str, port: int = 1883) -> None:
        self.robot_id = robot_id
        self.host, self.port = host, port
        self.connected = False
        self.rejected = 0
        self._commands: list[dict] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._conn = uuid.uuid4().hex[:12]

        name = f"robot-{robot_id}"
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=name,
            protocol=mqtt.MQTTv5,
        )
        self.client.username_pw_set(name, key)
        self.client.max_queued_messages_set(MAX_QUEUED)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self._set_will()

    def topic(self, channel: str) -> str:
        return f"fleet/{self.robot_id}/{channel}"

    def _presence(self, online: bool) -> str:
        return json.dumps({"online": online, "conn": self._conn})

    def _set_will(self) -> None:
        self.client.will_set(self.topic("presence"), self._presence(False), qos=1, retain=True)

    # ── paho thread ─────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.warning(
                "Robot %d: broker refused the connection: %s", self.robot_id, reason_code
            )
            return
        self.connected = True
        client.publish(self.topic("presence"), self._presence(True), qos=1, retain=True)
        client.subscribe(self.topic("commands"), qos=1)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = False
        # The next connection gets its own id and a will that names it.
        self._conn = uuid.uuid4().hex[:12]
        self._set_will()

    def _on_message(self, client, userdata, message) -> None:
        try:
            command = json.loads(message.payload)
        except ValueError:
            return
        self._loop.call_soon_threadsafe(self._commands.append, command)

    # ── asyncio side ────────────────────────────────────────────────

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = SESSION_EXPIRY_SECONDS
        self.client.connect_async(
            self.host, self.port, keepalive=15, clean_start=False, properties=props
        )
        self.client.loop_start()

    def stop(self) -> None:
        """A clean shutdown says offline itself; the will only covers crashes."""
        if self.connected:
            self.client.publish(self.topic("presence"), self._presence(False), qos=1, retain=True)
        self.client.disconnect()
        self.client.loop_stop()

    def publish(self, channel: str, body) -> bool:
        """Queue a QoS 1 message. False if paho's queue is full."""
        info = self.client.publish(self.topic(channel), json.dumps(body), qos=1)
        if info.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
            self.rejected += 1
            return False
        return True

    def take_commands(self) -> list[dict]:
        commands, self._commands = self._commands, []
        return commands

    def unacked(self) -> int:
        return len(self.client._out_messages)
