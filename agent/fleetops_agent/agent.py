"""The robot agent: one robot's connection to the FleetOps broker.

Everything the robot sends goes into the on-disk outbox first and leaves it
only when the server says it is stored::

    send_telemetry() ─► outbox (SQLite) ─► envelope ─► broker ─► server
                           ▲                                        │
                           └──── delete up to q ◄── fleet/<id>/stored (s, q)

So the guarantee is end to end. A broker PUBACK only means the broker has the
message; if the broker then dies inside its autosave interval the message is
gone, but the robot still holds it. Nothing advances the server's watermark
past the gap, and after ``resend_after`` seconds without progress the robot
sends everything above the watermark again. A robot process killed at any
point resends what it held on restart. Duplicates cost bandwidth only: the
server skips what it already has.

The robot logs in as ``robot-<id>`` with its device key on a persistent
session, so commands sent while it is offline wait for it. Its presence is a
retained ``online`` published on connect and a retained ``offline`` last
will, each naming the connection, so the server can ignore a late will from
an older connection.

Settings come from the server as desired config on the retained
``fleet/<id>/config`` topic. The agent applies the ones it knows
(``wire.CONFIG_FIELDS``), keeps them in the outbox file so a restart without
a broker still uses them, calls ``on_config``, and reports what it applied on
``fleet/<id>/config/reported``.

Two threads per agent: paho's network loop, and a sender that batches the
outbox into envelopes. The public methods are thread-safe and never block on
the network.
"""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from fleetops_agent import predict, wire
from fleetops_agent.outbox import Outbox

logger = logging.getLogger(__name__)

SESSION_EXPIRY_SECONDS = 7 * 24 * 3600


def _now_ms() -> int:
    return round(time.time() * 1000)


def _as_seen(packed: dict) -> dict:
    """A packed reading as the server will unpack it, times in epoch seconds."""
    values = wire.unpack_item("telemetry", packed)
    values["timestamp"] = values["timestamp"].timestamp()
    return values


def _runs(rows: list[tuple[int, str, dict]], limit: int):
    """Split outbox rows into envelopes: one channel, consecutive numbers, at most ``limit``."""
    run: list[tuple[int, str, dict]] = []
    for row in rows:
        if run and (row[1] != run[0][1] or row[0] != run[-1][0] + 1 or len(run) >= limit):
            yield run
            run = []
        run.append(row)
    if run:
        yield run


class Agent:
    def __init__(
        self,
        robot_id: int,
        key: str,
        host: str,
        port: int = 1883,
        *,
        outbox_path: str,
        max_outbox: int = 100_000,
        synchronous: str = "NORMAL",
        flush_interval: float = 1.0,
        batch_items: int = 100,
        window: int = 2000,
        resend_after: float = 15.0,
        keepalive: int = 15,
        ca_certs: str | None = None,
    ) -> None:
        """
        ``flush_interval``: how long readings wait to be batched. Events and
        command reports go out at once.
        ``window``: the most items sent but not yet confirmed, which bounds
        what a reconnect or a resend puts on the wire.
        ``resend_after``: seconds without a confirmation before everything
        unconfirmed is sent again.
        ``ca_certs``: enables TLS, verifying the broker against this CA file.
        """
        self.robot_id = robot_id
        self.host, self.port = host, port
        self.keepalive = keepalive
        self.flush_interval = flush_interval
        self.batch_items = batch_items
        self.window = window
        self.resend_after = resend_after
        self.outbox = Outbox(outbox_path, max_items=max_outbox, synchronous=synchronous)
        # Called from paho's thread with each new command, if set.
        self.on_command: Callable[[dict], None] | None = None
        # Called from paho's thread with the applied settings when they change.
        self.on_config: Callable[[dict], None] | None = None
        saved = self.outbox.get_meta("config") or {"version": 0, "values": {}}
        self._config_version: int = saved["version"]
        self._config: dict = saved["values"]
        self.stats = collections.Counter()

        self._lock = threading.Lock()
        self._connected = False
        self._stored = 0  # the server's watermark for this outbox's stream
        self._sent_upto = 0  # highest number handed to the broker
        self._last_progress = time.monotonic()
        self._commands: collections.deque[dict] = collections.deque()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._sender: threading.Thread | None = None
        self._conn = uuid.uuid4().hex[:12]
        self._rates = predict.RateEstimator()
        self._keyframe: dict | None = None  # the last keyframe, as the server sees it

        self._publish_props = Properties(PacketTypes.PUBLISH)
        self._publish_props.ContentType = wire.CONTENT_TYPE

        name = f"robot-{robot_id}"
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=name,
            protocol=mqtt.MQTTv5,
        )
        self.client.username_pw_set(name, key)
        if ca_certs:
            self.client.tls_set(ca_certs=ca_certs)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.max_inflight_messages_set(50)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self._set_will()

    # ── what the robot calls ────────────────────────────────────────

    def send_telemetry(self, reading: dict) -> int:
        """Queue a reading (field names as in ``wire.TELEMETRY_FIELDS``).

        ``timestamp`` defaults to now; pass the time the reading was taken if
        it is not now. Returns the reading's sequence number.
        """
        values = {"timestamp": time.time(), **reading}
        return self.outbox.put("telemetry", wire.pack_item("telemetry", values))

    def observe(self, reading: dict) -> bool:
        """Report a sensor reading; it is sent only if the server needs it.

        Dual prediction (``predict.py``): the reading becomes a keyframe when
        it strays from what the server is predicting by more than the
        tolerances in force, when a discrete field changes, or when the
        heartbeat is due. ``vx``, ``vy`` and ``battery_rate`` are estimated
        from successive readings unless given. Returns whether it was sent.
        """
        values = {"timestamp": time.time(), **reading}
        values["timestamp"] = wire.epoch_ms(values["timestamp"]) / 1000
        values = self._rates.update(values)
        packed = wire.pack_item("telemetry", values)
        seen = _as_seen(packed)
        reason = predict.keyframe_reason(self._keyframe, seen, predict.tolerances(self._config))
        self.stats["observed"] += 1
        if reason is None:
            return False
        self.outbox.put("telemetry", packed)
        self._keyframe = seen
        self.stats["keyframes"] += 1
        self.stats[f"keyframe:{reason}"] += 1
        return True

    def send_event(self, message: str, timestamp=None) -> int:
        values = {"timestamp": time.time() if timestamp is None else timestamp, "message": message}
        seq = self.outbox.put("events", wire.pack_item("events", values))
        self._wake.set()
        return seq

    def report_command(
        self,
        command_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        result: dict | None = None,
    ) -> int:
        """Report a command's progress: ACKNOWLEDGED, EXECUTING, COMPLETED or FAILED."""
        values = {
            "id": command_id,
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
            "result": result,
        }
        seq = self.outbox.put("commands/status", wire.pack_item("commands/status", values))
        self._wake.set()
        return seq

    def take_commands(self) -> list[dict]:
        """Commands received since the last call, each one only once ever."""
        taken = []
        while self._commands:
            taken.append(self._commands.popleft())
        return taken

    @property
    def config(self) -> dict:
        """The settings in force, as last applied from the server."""
        return dict(self._config)

    @property
    def config_version(self) -> int:
        return self._config_version

    @property
    def connected(self) -> bool:
        return self._connected

    def pending(self) -> int:
        """Items not yet confirmed as stored."""
        return len(self.outbox)

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until the server has confirmed everything; False on timeout."""
        self._wake.set()
        deadline = time.monotonic() + timeout
        while self.pending():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = SESSION_EXPIRY_SECONDS
        # clean_start=False on the first connection too, or the commands
        # queued while the robot was off would be discarded.
        self.client.connect_async(
            self.host, self.port, keepalive=self.keepalive, clean_start=False, properties=props
        )
        self.client.loop_start()
        self._sender = threading.Thread(
            target=self._send_loop, name=f"agent-{self.robot_id}", daemon=True
        )
        self._sender.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Say offline and disconnect. Unconfirmed items stay in the outbox."""
        self._stopping.set()
        self._wake.set()
        if self._sender is not None:
            self._sender.join(timeout)
        if self._connected:
            info = self.client.publish(
                self._topic("presence"), self._presence(False), qos=1, retain=True
            )
            info.wait_for_publish(timeout)
        self.client.disconnect()
        self.client.loop_stop()
        self.outbox.close()

    # ── sending ─────────────────────────────────────────────────────

    def _send_loop(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(self.flush_interval)
            self._wake.clear()
            try:
                self._send()
            except Exception:
                logger.exception("Robot %d: sending failed", self.robot_id)

    def _send(self) -> None:
        with self._lock:
            if not self._connected:
                return
            now = time.monotonic()
            if self._sent_upto > self._stored and now - self._last_progress > self.resend_after:
                # Nothing confirmed for a while: the broker may have lost what
                # it accepted. Start again from the watermark.
                self.stats["resends"] += 1
                self._sent_upto = self._stored
                self._last_progress = now
            start = self._sent_upto
            budget = self.window - max(0, start - self._stored)
        if budget <= 0:
            return
        rows = self.outbox.read(after=start, limit=budget)
        if not rows:
            return
        floor = self.outbox.floor()
        for run in _runs(rows, self.batch_items):
            channel = run[0][1]
            envelope = wire.Envelope(
                self.outbox.stream, run[0][0], floor, _now_ms(), [item for _, _, item in run]
            )
            info = self.client.publish(
                self._topic(channel),
                wire.encode_envelope(channel, envelope),
                qos=1,
                properties=self._publish_props,
            )
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                return
            with self._lock:
                if self._sent_upto <= self._stored:
                    # The first unconfirmed item: the resend clock starts now.
                    self._last_progress = time.monotonic()
                self._sent_upto = max(self._sent_upto, envelope.last_seq)
            self.stats["messages"] += 1
            self.stats["items"] += len(run)

    def _confirmed(self, stream: str, seq: int) -> None:
        if stream != self.outbox.stream:
            return  # the watermark of an outbox this robot no longer has
        with self._lock:
            if seq <= self._stored:
                return
            self._stored = seq
            self._sent_upto = max(self._sent_upto, seq)
            self._last_progress = time.monotonic()
        self.stats["confirmed"] += self.outbox.confirm(seq)
        self._wake.set()

    # ── paho thread ─────────────────────────────────────────────────

    def _topic(self, channel: str) -> str:
        return f"fleet/{self.robot_id}/{channel}"

    def _presence(self, online: bool) -> str:
        return json.dumps({"online": online, "conn": self._conn})

    def _set_will(self) -> None:
        self.client.will_set(self._topic("presence"), self._presence(False), qos=1, retain=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.warning(
                "Robot %d: broker refused the connection: %s", self.robot_id, reason_code
            )
            return
        client.publish(self._topic("presence"), self._presence(True), qos=1, retain=True)
        client.subscribe(
            [
                (self._topic("commands"), 1),
                (self._topic("stored"), 0),
                (self._topic("config"), 1),
            ]
        )
        with self._lock:
            self._connected = True
        self._wake.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        with self._lock:
            self._connected = False
        # The next connection gets its own id and a will that names it.
        self._conn = uuid.uuid4().hex[:12]
        self._set_will()

    def _on_message(self, client, userdata, message) -> None:
        channel = message.topic.rsplit("/", 1)[-1]
        try:
            if channel == "stored":
                self._confirmed(*wire.decode_stored(message.payload))
            elif channel == "commands":
                self._command(json.loads(message.payload))
            elif channel == "config":
                self._apply_config(*wire.decode_config(message.payload))
        except (ValueError, TypeError) as exc:
            logger.warning("Robot %d: ignoring a bad %s message: %s", self.robot_id, channel, exc)

    def _apply_config(self, version: int, values: dict) -> None:
        """Apply desired settings and report the outcome.

        The server's desired state wins even if its version is lower than the
        one applied (its database was restored, say). Reported every time, so
        a lost report is repaired by the next delivery.
        """
        applied, rejected = wire.check_config(values)
        changed = applied != self._config or version != self._config_version
        if changed:
            self.outbox.set_meta("config", {"version": version, "values": applied})
            self._config_version, self._config = version, applied
            self.stats["configs"] += 1
        self.client.publish(
            self._topic("config/reported"),
            wire.encode_reported(version, applied, rejected),
            qos=1,
            retain=True,
        )
        if rejected:
            logger.warning("Robot %d: settings refused: %s", self.robot_id, rejected)
        if changed and self.on_config is not None:
            self.on_config(dict(applied))

    def _command(self, command) -> None:
        if not isinstance(command, dict) or not isinstance(command.get("id"), str):
            raise ValueError("a command needs an id")
        # A command can be pushed more than once (again on reconnect); run it once.
        if not self.outbox.first_sighting(command["id"], _now_ms()):
            self.stats["repeated_commands"] += 1
            return
        self.stats["commands"] += 1
        self._commands.append(command)
        if self.on_command is not None:
            self.on_command(command)
