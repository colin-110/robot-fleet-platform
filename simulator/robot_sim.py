import argparse
import asyncio
import contextlib
import logging
import math
import multiprocessing
import os
import random
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import aiohttp

try:
    import uvloop

    uvloop.install()
except ImportError:
    pass

MISSION_TYPES = ["PATROL", "DELIVERY", "INSPECTION"]


@dataclass
class MissionStep:
    x: float
    y: float
    label: str
    pause_s: float = 0.0


@dataclass
class Mission:
    mission_id: str
    mission_type: str
    steps: list[MissionStep]
    home_x: float
    home_y: float
    progress_weight: float
    current_step: int = 0


@dataclass
class RobotState:
    robot_id: int
    battery: float = 100.0
    temperature: float = 33.0
    speed: float = 0.0
    status: str = "ACTIVE"
    online: bool = True
    mission: Mission | None = None
    mission_id: str | None = None
    mission_progress: float | None = None
    mission_start_time: str | None = None
    battery_health: float = 100.0
    motor_health: float = 100.0
    sensor_health: float = 100.0
    network_health: float = 100.0
    dead_printed: bool = False
    x: float = 0.0
    y: float = 0.0
    home_x: float = 0.0
    home_y: float = 0.0
    last_update_s: float = 0.0
    pause_until_s: float = 0.0
    completion_count: int = 0
    blackout_until: float = 0.0
    charging_suspended: bool = False
    dead_since: float = 0.0
    returning_to_charge: bool = False
    processed_command_ids: list[str] = field(default_factory=list)
    in_fence: bool = False
    fence_cooldown_until: float = 0.0
    # Seconds between readings when the server has set one (desired config).
    report_interval_s: float | None = None


def tick_seconds(robot: RobotState, rng: random.Random, tick_min_s: float, tick_max_s: float):
    """How long the robot waits before its next tick."""
    if robot.report_interval_s:
        return robot.report_interval_s * rng.uniform(0.9, 1.1)
    return rng.uniform(tick_min_s, tick_max_s)


def clamp(value: float, lo: float, hi: float):
    return max(lo, min(hi, value))


def get_base_api(api_url: str) -> str:
    parsed = urlparse(api_url)
    path = parsed.path
    if path.endswith("/telemetry"):
        path = path[: -len("/telemetry")]
    elif path.endswith("/telemetry/"):
        path = path[: -len("/telemetry/")]
    return urlunparse(parsed._replace(path=path))


def lerp(a: float, b: float, t: float):
    return a + (b - a) * t


# ── Rates ────────────────────────────────────────────────────────────
# Everything below is per second of wall-clock time, so a different tick rate
# does not change how fast robots drain, charge or wear. The battery and wear
# models below are written at real-world scale and sped up here, by one
# factor each, so a demo shows a full cycle in under an hour. --time-scale
# multiplies all three (2 = everything twice as fast).
#
# At the default: a robot on a mission drains about 2.6 % a minute (a full
# battery lasts about half an hour of driving), charges about 15 % a minute,
# and its components lose about 5 % of their health an hour.
DRAIN_SPEEDUP = 1.5
CHARGE_PERCENT_PER_S = 0.25
WEAR_SPEEDUP = 1.5
# An idle robot below this goes back to charge before it is given a mission.
IDLE_TOP_UP_PERCENT = 35.0
# A brief, hard heat spike (a jammed wheel, direct sun), per second.
HEAT_SPIKE_PER_S = 0.0003
# Network blackouts, per second: a base rate plus more for a worn network
# interface. Each lasts 10 to 25 seconds.
BLACKOUT_BASE_PER_S = 0.0002
BLACKOUT_WEAR_PER_S = 0.002

# Robots one simulator process runs over MQTT. Each robot's agent holds about
# six file descriptors (its socket, paho's wake-up socket pair, the outbox's
# SQLite files), and paho waits on its sockets with select(), which cannot
# watch a descriptor numbered 1024 or above: past about 160 robots in one
# process, the rest connect, fail and reconnect forever without an error.
# --workers is raised to keep every process under this.
ROBOTS_PER_PROCESS = 120

# Components wear down to this and no further. A battery or motor that gets
# here has failed: the robot shuts down and a repair crew replaces it.
HEALTH_FLOOR = 10.0

# Set from --time-scale in main().
TIME_SCALE = 1.0


def has_failed(robot) -> bool:
    """Flat battery, meltdown, or a battery or motor worn out."""
    return (
        robot.battery <= 5.0
        or robot.temperature >= 95.0
        or robot.battery_health <= HEALTH_FLOOR
        or robot.motor_health <= HEALTH_FLOOR
    )


def initial_component_health(rng: random.Random, service_age: float | None = None):
    """Component health for a robot entering the fleet.

    Every robot used to start at exactly 100.0. Combined with a wear rate of
    roughly one percent per hour, that meant all four health readouts sat at a
    flat 100 for the first several hours of uptime: the colour thresholds never
    fired, the per-component bars were indistinguishable, and the maintenance
    view had nothing to rank. Real fleets are mixed-age, so this seeds one.

    A single service-age factor drives all four components, because a unit that
    has done ten thousand hours is worn everywhere rather than in one subsystem.
    Per-component jitter keeps them from moving in visible lockstep, and the
    spreads differ because motors wear faster than network interfaces.

    Returns ``(battery, motor, sensor, network)``.
    """
    # Beta(2, 3) skews toward mid-life: mostly serviceable units, a few nearly
    # new, a few due for replacement.
    age = rng.betavariate(2.0, 3.0) if service_age is None else service_age

    def component(max_wear: float) -> float:
        return clamp(100.0 - age * max_wear * rng.uniform(0.75, 1.25), 25.0, 100.0)

    return component(55.0), component(70.0), component(45.0), component(40.0)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("simulator")


def safe_print(message: str):
    logger.info(message)


def random_point(rng: random.Random, radius: float):
    angle = rng.random() * math.tau
    distance = radius * math.sqrt(rng.random())
    return (math.cos(angle) * distance, math.sin(angle) * distance)


def iso_utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_mission(mission_id: str, mission_type: str, *, rng: random.Random, radius: float):
    if mission_type == "PATROL":
        checkpoint_count = rng.randint(3, 5)
        checkpoints = [
            MissionStep(*random_point(rng, radius), label=f"Checkpoint {index + 1}")
            for index in range(checkpoint_count)
        ]
        checkpoints.append(MissionStep(0.0, 0.0, label="Return to base"))
        return Mission(
            mission_id=mission_id,
            mission_type=mission_type,
            steps=checkpoints,
            home_x=0.0,
            home_y=0.0,
            progress_weight=100.0 / len(checkpoints),
        )

    if mission_type == "DELIVERY":
        pickup = MissionStep(*random_point(rng, radius * 0.8), label="Pickup")
        dropoff = MissionStep(*random_point(rng, radius), label="Delivery")
        steps = [pickup, dropoff, MissionStep(0.0, 0.0, label="Return to base")]
        return Mission(
            mission_id=mission_id,
            mission_type=mission_type,
            steps=steps,
            home_x=0.0,
            home_y=0.0,
            progress_weight=100.0 / len(steps),
        )

    inspection_count = rng.randint(2, 4)
    steps = [
        MissionStep(
            *random_point(rng, radius * 0.9),
            label=f"Inspection {index + 1}",
            pause_s=rng.uniform(2.0, 4.0),
        )
        for index in range(inspection_count)
    ]
    return Mission(
        mission_id=mission_id,
        mission_type=mission_type,
        steps=steps,
        home_x=0.0,
        home_y=0.0,
        progress_weight=100.0 / len(steps),
    )


def assign_mission(robot: RobotState, mission: Mission):
    robot.mission = mission
    robot.mission_id = mission.mission_id
    robot.mission_progress = 0.0
    robot.mission_start_time = iso_utc_now()
    robot.status = "ACTIVE"
    robot.pause_until_s = 0.0
    robot.returning_to_charge = False


def clear_mission(robot: RobotState):
    robot.mission = None
    robot.mission_id = None
    robot.mission_progress = None
    robot.mission_start_time = None
    robot.pause_until_s = 0.0


def effective_speed(robot: RobotState, mission_type: str, rng: random.Random):
    base = {
        "PATROL": 1.1,
        "DELIVERY": 1.8,
        "INSPECTION": 0.95,
        "RETURN": 1.4,
    }.get(mission_type, 1.0)
    degraded_cap = base * clamp(robot.motor_health / 100.0, 0.45, 1.0)
    return clamp(degraded_cap + rng.uniform(-0.08, 0.12), 0.2, 2.2)


def apply_sensor_noise(robot: RobotState, value: float, *, kind: str, rng: random.Random):
    """Measurement noise, growing as the sensors wear.

    Sized like real sensors: a battery's state of charge comes from its
    management system already smoothed, so it moves by tenths of a percent,
    not whole percents; a temperature probe jitters by up to a degree; and only
    the physical sensors (temperature, wheel speed) occasionally glitch. Noise
    larger than the agent's tolerances would make every reading a keyframe.
    """
    noise_scale = (100.0 - robot.sensor_health) / 100.0
    if noise_scale <= 0:
        return value

    if kind == "temperature":
        value += rng.uniform(-1.0, 1.0) * noise_scale
    elif kind == "speed":
        value += rng.uniform(-0.12, 0.12) * noise_scale
    else:
        value += rng.uniform(-0.3, 0.3) * noise_scale

    if kind != "battery" and rng.random() < noise_scale * 0.01:
        value += rng.uniform(-6.0, 6.0)

    return value


# Per-robot device keys, filled at startup when --device-keys is set. Empty
# means every request uses the shared key from the session headers.
_device_keys: dict[int, str] = {}


# One agent (fleetops_agent.Agent) per robot, filled at startup when
# --mqtt-host is set. A robot with an agent sends everything over MQTT
# through the agent's on-disk outbox instead of HTTP.
_agents: dict = {}
# --every-reading: send every reading instead of only the keyframes dual
# prediction needs (for comparing the two).
_every_reading = False


def key_headers(robot_id: int) -> dict | None:
    """Headers that authenticate as ``robot_id``, or None to use the shared key."""
    key = _device_keys.get(robot_id)
    return {"X-API-Key": key} if key else None


async def provision_device_keys(
    client: aiohttp.ClientSession, base_api: str, robot_ids: list[int], timeout_s: float
) -> None:
    """Issue a device key for each robot using the shared (admin) key.

    Issuing rotates the key, so a restarted simulator invalidates the keys of
    its previous run.
    """
    for robot_id in robot_ids:
        for attempt in range(5):
            try:
                async with client.post(
                    f"{base_api}/robots/{robot_id}/key", timeout=timeout_s
                ) as response:
                    response.raise_for_status()
                    _device_keys[robot_id] = (await response.json())["key"]
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 4:
                    raise RuntimeError(f"Could not issue a key for robot {robot_id}") from exc
                await asyncio.sleep(2**attempt)
    safe_print(f"[KEYS] Issued device keys for {len(robot_ids)} robots.")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Outbox:
    """Readings kept until the server has accepted them.

    A reading stays here until a POST containing it succeeds, so a failed
    upload or an unreachable server costs nothing but a retry. The server
    skips readings it already has, so resending is safe.
    """

    MAX_PER_ROBOT = 5000

    def __init__(self) -> None:
        # robot_id -> timestamp -> payload
        self._entries: dict[int, dict[str, dict]] = {}
        self.generated = 0
        self.dropped = 0

    def add(self, payload: dict) -> None:
        entries = self._entries.setdefault(payload["robot_id"], {})
        entries[payload["timestamp"]] = payload
        self.generated += 1
        if len(entries) > self.MAX_PER_ROBOT:
            entries.pop(next(iter(entries)))
            self.dropped += 1

    def unsent(self) -> list[dict]:
        return [payload for entries in self._entries.values() for payload in entries.values()]

    def confirm(self, payloads: list[dict]) -> None:
        """Forget readings the server accepted."""
        for payload in payloads:
            self._entries.get(payload["robot_id"], {}).pop(payload["timestamp"], None)

    def discard(self, payloads: list[dict]) -> None:
        """Forget readings the server rejected outright (they would never succeed)."""
        self.confirm(payloads)
        self.dropped += len(payloads)

    def pending_count(self) -> int:
        return sum(len(entries) for entries in self._entries.values())


class PermanentRejection(Exception):
    """The server refused the readings in a way a retry will not fix."""


async def post_telemetry(
    client: aiohttp.ClientSession,
    api_url: str,
    payload: dict | list,
    timeout_s: float,
    headers: dict | None = None,
) -> dict:
    """POST readings and return the response body."""
    for attempt in range(3):
        try:
            async with client.post(
                api_url, json=payload, timeout=timeout_s, headers=headers
            ) as response:
                if 400 <= response.status < 500 and response.status not in (408, 429):
                    raise PermanentRejection(f"{response.status}: {await response.text()}")
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == 2:
                raise exc
            await asyncio.sleep(0.5 * (2**attempt))
    raise RuntimeError("unreachable")


async def patch_command_status(
    client: aiohttp.ClientSession,
    base_api: str,
    robot_id: int,
    cmd_id: str,
    status: str,
    **extra,
) -> None:
    """Best-effort command status transition.

    Failures are logged and swallowed: a robot that can't report its progress
    should keep executing the command, and the backend's timeout sweeper will
    reconcile anything left dangling.
    """
    payload = {"status": status, **extra}
    agent = _agents.get(robot_id)
    if agent is not None:
        agent.report_command(cmd_id, status, **extra)
        return
    try:
        async with client.patch(
            f"{base_api}/commands/{cmd_id}/status", json=payload, headers=key_headers(robot_id)
        ) as response:
            await response.read()
    except Exception as exc:
        logger.debug("Failed to patch command %s to %s: %s", cmd_id, status, exc)


async def send_event(client: aiohttp.ClientSession, base_api: str, robot_id: int, message: str):
    """Best-effort event. The device timestamp makes a resent event idempotent."""
    agent = _agents.get(robot_id)
    if agent is not None:
        agent.send_event(message)
        return
    body = {"robot_id": robot_id, "message": message, "timestamp": iso_utc_now()}
    try:
        async with client.post(
            f"{base_api}/events", json=body, timeout=10.0, headers=key_headers(robot_id)
        ) as response:
            await response.read()
    except Exception:
        pass


async def dispatcher_loop(
    *, robots: list[RobotState], queue: list[Mission], rng: random.Random, radius: float
):
    mission_counter = 1
    while True:
        if len(queue) < 4:
            mission_type = rng.choice(MISSION_TYPES)
            mission_id = f"M-{mission_counter:05d}"
            mission_counter += 1
            queue.append(build_mission(mission_id, mission_type, rng=rng, radius=radius))

        for mission in list(queue):
            candidates = [
                robot
                for robot in robots
                if robot.online
                # STOPPED robots stay parked until an explicit RESUME command -
                # the dispatcher must not re-task an emergency-stopped unit.
                and robot.status not in {"DEAD", "CHARGING", "OVERHEATING", "STOPPED"}
                and robot.mission is None
                and not robot.returning_to_charge
                and robot.battery > 20.0
            ]
            if not candidates:
                break

            candidates.sort(
                key=lambda robot: math.hypot(
                    mission.steps[0].x - robot.x,
                    mission.steps[0].y - robot.y,
                )
            )
            selected = candidates[0]
            assign_mission(selected, mission)
            queue.remove(mission)
            safe_print(
                f"[DISPATCH] assigned {mission.mission_type} {mission.mission_id} "
                f"to R{selected.robot_id:02d}"
            )

        await asyncio.sleep(3.0)


async def handle_commands(
    robot: RobotState, commands: list[dict], client: aiohttp.ClientSession, base_api: str
):
    """Run commands, reporting each step. A command seen before is not run again."""
    for cmd_obj in commands:
        cmd_id = cmd_obj["id"]
        cmd_action = cmd_obj.get("command_type") or cmd_obj.get("action")

        if cmd_id in robot.processed_command_ids:
            # End-to-end idempotency: replay the result
            # rather than executing the command twice.
            await patch_command_status(
                client,
                base_api,
                robot.robot_id,
                cmd_id,
                "COMPLETED",
                result={"message": "Already processed"},
            )
            continue

        robot.processed_command_ids.append(cmd_id)
        if len(robot.processed_command_ids) > 100:
            robot.processed_command_ids.pop(0)

        await patch_command_status(client, base_api, robot.robot_id, cmd_id, "ACKNOWLEDGED")
        await patch_command_status(client, base_api, robot.robot_id, cmd_id, "EXECUTING")

        status_to_patch = "COMPLETED"

        if cmd_action == "RETURN_TO_BASE":
            clear_mission(robot)
            robot.returning_to_charge = True
            robot.status = "ACTIVE"
            robot.online = True
            safe_print(f"[R{robot.robot_id:02d}] Executing RETURN_TO_BASE command (id={cmd_id})")
        elif cmd_action == "EMERGENCY_STOP":
            robot.status = "STOPPED"
            robot.speed = 0.0
            clear_mission(robot)
            robot.returning_to_charge = False
            safe_print(f"[R{robot.robot_id:02d}] EMERGENCY STOP ACTIVATED (id={cmd_id})")
        elif cmd_action == "RESUME":
            robot.status = "ACTIVE"
            robot.online = True
            safe_print(f"[R{robot.robot_id:02d}] RESUMED (id={cmd_id})")
        else:
            status_to_patch = "FAILED"

        await patch_command_status(client, base_api, robot.robot_id, cmd_id, status_to_patch)


async def robot_loop(
    robot: RobotState,
    *,
    client: aiohttp.ClientSession,
    api_url: str,
    queue: asyncio.Queue,
    rng: random.Random,
    ambient_c: float,
    tick_min_s: float,
    tick_max_s: float,
    post_timeout_s: float,
):
    base_api = get_base_api(api_url)
    robot.last_update_s = time.time()

    try:
        while True:
            now = time.time()
            dt = clamp(now - robot.last_update_s, 0.1, 2.5)
            robot.last_update_s = now

            # Check simulated network blackout
            is_blacked_out = robot.blackout_until > now
            agent = _agents.get(robot.robot_id)
            if agent is not None and agent.link_down and not is_blacked_out:
                agent.restore_link()
                safe_print(
                    f"[R{robot.robot_id:02d}] Network back; sending {agent.pending()} "
                    "readings held on the robot"
                )

            # Trigger network blackout with random probability
            if not is_blacked_out and robot.status not in ("DEAD", "STOPPED"):
                blackout_chance = (
                    BLACKOUT_BASE_PER_S
                    + ((100.0 - robot.network_health) / 100.0) * BLACKOUT_WEAR_PER_S
                ) * dt
                if rng.random() < blackout_chance:
                    blackout_duration = rng.uniform(10.0, 25.0)
                    robot.blackout_until = now + blackout_duration
                    is_blacked_out = True
                    safe_print(
                        f"[R{robot.robot_id:02d}] Network blackout for {blackout_duration:.1f}s (network_health={robot.network_health:.1f}%)"
                    )
                    # Over MQTT this is a real link loss: the broker publishes
                    # the robot's will (offline), and the robot keeps measuring
                    # into its agent's outbox, which sends everything once the
                    # link is back. Over HTTP the readings are simply lost.
                    if agent is not None:
                        agent.drop_link()

            # Whether readings and events go anywhere this tick.
            can_send = agent is not None or not is_blacked_out

            # Commands are handled during a telemetry blackout too, so an
            # operator's Stop still lands. Over MQTT they were pushed to the
            # robot; over HTTP it claims them (POST /claim, not GET: claiming
            # transitions commands to DISPATCHED, so it is not a safe method).
            try:
                if agent is not None:
                    await handle_commands(robot, agent.take_commands(), client, base_api)
                else:
                    cmd_url = f"{base_api}/commands/{robot.robot_id}/claim"
                    async with client.post(
                        cmd_url, timeout=10.0, headers=key_headers(robot.robot_id)
                    ) as cmd_resp:
                        if cmd_resp.status == 200:
                            await handle_commands(robot, await cmd_resp.json(), client, base_api)
            except Exception as e:
                logger.error(f"[R{robot.robot_id:02d}] Command poll error: {e}", exc_info=True)

            # Meltdown / Battery exhaustion DEAD checks
            if has_failed(robot):
                if robot.status != "DEAD":
                    robot.status = "DEAD"
                    robot.online = False
                    robot.speed = 0.0
                    robot.dead_since = now
                    clear_mission(robot)
                    robot.returning_to_charge = False
                    safe_print(
                        f"[R{robot.robot_id:02d}] SHUTDOWN: DEAD state reached (bat={robot.battery:.1f}%, temp={robot.temperature:.1f}C, motor_h={robot.motor_health:.1f}%)"
                    )

                # Emit dead telemetry if not blacked out
                if can_send:
                    await emit_telemetry(robot, queue, rng)

                # Maintenance repair crew event (45s to 90s delay)
                if now - robot.dead_since >= rng.uniform(45.0, 90.0):
                    robot.battery = 100.0
                    robot.temperature = ambient_c
                    # A replacement unit, not a magically restored one. Reviving
                    # everything to a flat 100 pulled the whole fleet toward
                    # perfect health over a long run, undoing the age spread.
                    (
                        robot.battery_health,
                        robot.motor_health,
                        robot.sensor_health,
                        robot.network_health,
                    ) = initial_component_health(rng, service_age=rng.uniform(0.02, 0.15))
                    robot.status = "ACTIVE"
                    robot.online = True
                    robot.charging_suspended = False
                    safe_print(
                        f"[R{robot.robot_id:02d}] MAINTENANCE COMPLETE: Robot fully revived and operational"
                    )

                await asyncio.sleep(tick_seconds(robot, rng, tick_min_s, tick_max_s))
                continue

            if robot.status == "STOPPED":
                if can_send:
                    await emit_telemetry(robot, queue, rng)
                await asyncio.sleep(tick_seconds(robot, rng, tick_min_s, tick_max_s))
                continue

            # Low power / Return to charge triggers
            if robot.battery <= 30.0 and robot.status not in ("CHARGING", "RETURNING_TO_CHARGE"):
                robot.status = "LOW POWER"
                if robot.battery <= 25.0 and robot.mission is not None:
                    # Abort active mission to top up
                    safe_print(
                        f"[R{robot.robot_id:02d}] Aborting mission {robot.mission_id} due to low charge ({robot.battery:.1f}%)"
                    )
                    clear_mission(robot)
                    robot.returning_to_charge = True

            # Proactive top up if idle
            if (
                robot.mission is None
                and robot.battery <= IDLE_TOP_UP_PERCENT
                and not robot.returning_to_charge
                and robot.status != "CHARGING"
            ):
                robot.returning_to_charge = True

            # ── STATE PHYSICS ──

            # 1. Returning to charge base
            if robot.returning_to_charge:
                robot.status = "LOW POWER" if robot.battery <= 20.0 else "ACTIVE"
                dx = 0.0 - robot.x
                dy = 0.0 - robot.y
                dist = math.hypot(dx, dy)
                if dist < 0.5:
                    robot.returning_to_charge = False
                    robot.status = "CHARGING"
                    robot.x = 0.0
                    robot.y = 0.0
                    robot.speed = 0.0
                    safe_print(f"[R{robot.robot_id:02d}] Arrived at Base charging pad.")
                else:
                    target_speed = effective_speed(robot, "RETURN", rng)
                    robot.speed = lerp(robot.speed, target_speed, 0.22)
                    step_distance = robot.speed * dt
                    robot.x += (dx / dist) * step_distance
                    robot.y += (dy / dist) * step_distance

                    motor_penalty = 1.0 + ((100.0 - robot.motor_health) / 100.0) * 0.45
                    battery_penalty = 1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.65
                    drain_per_s = (0.008 + 0.014 * robot.speed) * motor_penalty * battery_penalty
                    robot.battery -= drain_per_s * dt * DRAIN_SPEEDUP * TIME_SCALE

                    load_heat = (
                        0.055 + 0.055 * robot.speed + ((100.0 - robot.motor_health) / 100.0) * 0.035
                    )
                    cooling = 0.02 * max(0.0, robot.temperature - ambient_c)
                    robot.temperature += (load_heat - cooling) * dt

            # 2. Charging (with thermal suspension)
            elif robot.status == "CHARGING":
                robot.speed = 0.0

                if robot.temperature >= 80.0 and not robot.charging_suspended:
                    robot.charging_suspended = True
                    safe_print(
                        f"[R{robot.robot_id:02d}] Thermal safety: Charging suspended due to overheating ({robot.temperature:.1f}C)"
                    )

                if robot.charging_suspended:
                    robot.status = "OVERHEATING"
                    # Cool down
                    robot.temperature -= 0.08 * (robot.temperature - ambient_c) * dt
                    if robot.temperature <= 60.0:
                        robot.charging_suspended = False
                        robot.status = "CHARGING"
                        safe_print(
                            f"[R{robot.robot_id:02d}] Battery cooled to safe levels. Resuming charge cycle."
                        )
                else:
                    charge_rate = CHARGE_PERCENT_PER_S * clamp(
                        robot.battery_health / 100.0, 0.55, 1.0
                    )
                    robot.battery = clamp(robot.battery + charge_rate * dt * TIME_SCALE, 0.0, 100.0)

                    # Charging creates heat
                    charging_heat = 0.12 * (1.0 + (100.0 - robot.battery_health) / 100.0)
                    cooling = 0.035 * (robot.temperature - ambient_c)
                    robot.temperature += (charging_heat - cooling) * dt

                    if robot.battery >= 100.0:
                        robot.status = "ACTIVE"
                        safe_print(f"[R{robot.robot_id:02d}] Fully charged to 100%.")

            # 3. Overheating operation (speed halved)
            elif robot.temperature >= 80.0:
                robot.status = "OVERHEATING"
                if robot.mission is not None:
                    mission = robot.mission
                    step = mission.steps[mission.current_step]
                    dx = step.x - robot.x
                    dy = step.y - robot.y
                    dist = math.hypot(dx, dy)

                    # Slow down by half to cool down
                    target_speed = effective_speed(robot, mission.mission_type, rng) / 2.0
                    robot.speed = lerp(robot.speed, target_speed, 0.22)

                    if dist < 0.45:
                        mission.current_step += 1
                        robot.mission_progress = round(
                            min(100.0, mission.current_step * mission.progress_weight), 1
                        )
                        if mission.current_step >= len(mission.steps):
                            robot.completion_count += 1
                            robot.mission_progress = 100.0
                            safe_print(
                                f"[R{robot.robot_id:02d}] Completed mission {mission.mission_id} while overheating."
                            )
                            if can_send:
                                await emit_telemetry(robot, queue, rng)
                            clear_mission(robot)
                        else:
                            next_step = mission.steps[mission.current_step]
                    else:
                        step_distance = robot.speed * dt
                        robot.x += (dx / dist) * step_distance
                        robot.y += (dy / dist) * step_distance

                    # Lower battery drain
                    motor_penalty = 1.0 + ((100.0 - robot.motor_health) / 100.0) * 0.45
                    battery_penalty = 1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.65
                    drain_per_s = (0.008 + 0.014 * robot.speed) * motor_penalty * battery_penalty
                    robot.battery -= drain_per_s * dt * DRAIN_SPEEDUP * TIME_SCALE

                    load_heat = 0.02 + 0.02 * robot.speed
                    cooling = 0.045 * (robot.temperature - ambient_c)
                    robot.temperature += (load_heat - cooling) * dt
                else:
                    robot.speed = lerp(robot.speed, 0.0, 0.35)
                    robot.temperature -= 0.06 * (robot.temperature - ambient_c) * dt

            # 4. Normal Mission Operation
            elif robot.mission is not None:
                mission = robot.mission
                step = mission.steps[mission.current_step]
                dx = step.x - robot.x
                dy = step.y - robot.y
                dist = math.hypot(dx, dy)

                if robot.pause_until_s > now:
                    robot.speed = lerp(robot.speed, 0.0, 0.4)
                else:
                    target_speed = effective_speed(robot, mission.mission_type, rng)
                    robot.speed = lerp(robot.speed, target_speed, 0.22)

                    if dist < 0.45:
                        mission.current_step += 1
                        robot.mission_progress = round(
                            min(100.0, mission.current_step * mission.progress_weight),
                            1,
                        )

                        if step.pause_s > 0:
                            robot.pause_until_s = now + step.pause_s

                        if mission.current_step >= len(mission.steps):
                            robot.completion_count += 1
                            robot.mission_progress = 100.0
                            safe_print(
                                f"[R{robot.robot_id:02d}] completed "
                                f"{mission.mission_type} {mission.mission_id}"
                            )
                            if can_send:
                                await emit_telemetry(robot, queue, rng)
                                await send_event(
                                    client,
                                    base_api,
                                    robot.robot_id,
                                    f"Completed {mission.mission_type} mission {mission.mission_id}",
                                )
                            clear_mission(robot)
                            robot.status = "ACTIVE"
                        else:
                            next_step = mission.steps[mission.current_step]
                            safe_print(
                                f"[R{robot.robot_id:02d}] {mission.mission_id} -> {next_step.label}"
                            )
                    else:
                        step_distance = robot.speed * dt
                        robot.x += (dx / dist) * step_distance
                        robot.y += (dy / dist) * step_distance

                motor_penalty = 1.0 + ((100.0 - robot.motor_health) / 100.0) * 0.45
                battery_penalty = 1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.65
                drain_per_s = (0.008 + 0.014 * robot.speed) * motor_penalty * battery_penalty
                robot.battery -= drain_per_s * dt * DRAIN_SPEEDUP * TIME_SCALE

                load_heat = (
                    0.055 + 0.055 * robot.speed + ((100.0 - robot.motor_health) / 100.0) * 0.035
                )
                cooling = 0.02 * max(0.0, robot.temperature - ambient_c)
                robot.temperature += (load_heat - cooling) * dt

            # 5. Standby
            else:
                robot.speed = lerp(robot.speed, 0.0, 0.35)
                standby_drain = 0.0015 * (1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.25)
                robot.battery -= standby_drain * dt * DRAIN_SPEEDUP * TIME_SCALE
                robot.temperature -= 0.04 * (robot.temperature - ambient_c) * dt

            # Wear down components, per second. The rates are about 0.5 % an
            # hour each before WEAR_SPEEDUP; motors wear faster on a mission.
            wear = dt * WEAR_SPEEDUP * TIME_SCALE
            robot.battery_health = clamp(
                robot.battery_health - rng.uniform(0.00034, 0.00069) * wear, HEALTH_FLOOR, 100.0
            )
            robot.motor_health = clamp(
                robot.motor_health
                - rng.uniform(0.00046, 0.0008) * (1.3 if robot.mission else 0.5) * wear,
                HEALTH_FLOOR,
                100.0,
            )
            robot.sensor_health = clamp(
                robot.sensor_health - rng.uniform(0.00029, 0.00057) * wear, HEALTH_FLOOR, 100.0
            )
            robot.network_health = clamp(
                robot.network_health - rng.uniform(0.00034, 0.00063) * wear, HEALTH_FLOOR, 100.0
            )

            if rng.random() < HEAT_SPIKE_PER_S * dt:
                robot.temperature += rng.uniform(4.0, 9.0)

            # Check geofence. Hysteresis (enter <5m, clear only after >7m) stops a
            # robot hovering at the boundary from oscillating, and a per-robot
            # cooldown guarantees we never spam the same entry event.
            dist_to_fence = math.hypot(robot.x - 15.0, robot.y - 10.0)
            if dist_to_fence < 5.0 and not robot.in_fence:
                robot.in_fence = True
                if now >= robot.fence_cooldown_until and can_send:
                    robot.fence_cooldown_until = now + 45.0
                    safe_print(f"[R{robot.robot_id:02d}] ENTERED RESTRICTED ZONE")
                    await send_event(client, base_api, robot.robot_id, "Entered Restricted Zone!")
            elif dist_to_fence > 7.0 and robot.in_fence:
                robot.in_fence = False

            robot.battery = clamp(robot.battery, 0.0, 100.0)
            robot.temperature = clamp(robot.temperature, 22.0, 99.0)
            robot.speed = clamp(robot.speed, 0.0, 2.2)

            # Emit telemetry if online and not in blackout
            if can_send:
                await emit_telemetry(robot, queue, rng)

            await asyncio.sleep(tick_seconds(robot, rng, tick_min_s, tick_max_s))

    except asyncio.CancelledError:
        safe_print(f"[R{robot.robot_id:02d}] Decommissioned and shutting down.")


async def emit_telemetry(robot: RobotState, queue: asyncio.Queue, rng: random.Random):

    payload = {
        "robot_id": robot.robot_id,
        # The robot stamps its own reading. Readings sit in an outbound queue
        # and are posted in batches, so a server-side timestamp would record
        # when the batch was uploaded rather than when each was measured -
        # collapsing readings taken seconds apart onto one instant.
        "timestamp": iso_utc_now(),
        "battery": round(
            clamp(apply_sensor_noise(robot, robot.battery, kind="battery", rng=rng), 0.0, 100.0), 2
        ),
        "temperature": round(
            clamp(
                apply_sensor_noise(robot, robot.temperature, kind="temperature", rng=rng),
                0.0,
                120.0,
            ),
            2,
        ),
        "speed": round(
            clamp(apply_sensor_noise(robot, robot.speed, kind="speed", rng=rng), 0.0, 3.0), 2
        ),
        "status": robot.status,
        "mission_id": robot.mission_id,
        "mission_type": robot.mission.mission_type if robot.mission else None,
        "mission_progress": robot.mission_progress,
        "mission_start_time": robot.mission_start_time,
        "battery_health": round(robot.battery_health, 2),
        "motor_health": round(robot.motor_health, 2),
        "sensor_health": round(robot.sensor_health, 2),
        "network_health": round(robot.network_health, 2),
        "x": round(robot.x, 2),
        "y": round(robot.y, 2),
    }

    try:
        agent = _agents.get(robot.robot_id)
        if agent is not None:
            # The agent keeps only the keyframes the server's prediction
            # needs, in its outbox, and batches them.
            reading = {k: v for k, v in payload.items() if k != "robot_id"}
            reading["timestamp"] = _parse_ts(reading["timestamp"])
            if reading["mission_start_time"]:
                reading["mission_start_time"] = _parse_ts(reading["mission_start_time"])
            if _every_reading:
                agent.send_telemetry(reading)
            else:
                agent.observe(reading)
        else:
            await queue.put(payload)
        mission_label = (
            robot.mission.mission_type
            if robot.mission
            else ("RTB" if robot.returning_to_charge else "IDLE")
        )
        progress = (
            f"{robot.mission_progress:5.1f}%" if robot.mission_progress is not None else "  n/a"
        )
        logger.debug(
            f"[R{robot.robot_id:02d}] {robot.status:<10} {mission_label:<10} "
            f"bat={robot.battery:5.1f}% temp={robot.temperature:5.1f}C spd={robot.speed:4.2f} "
            f"mission={progress} comp=({robot.battery_health:4.0f}/{robot.motor_health:4.0f}/"
            f"{robot.sensor_health:4.0f}/{robot.network_health:4.0f}) pos=({robot.x:5.1f},{robot.y:5.1f})"
        )
    except Exception as exc:
        safe_print(f"[R{robot.robot_id:02d}] Queue put failed: {exc}")


async def telemetry_batcher(
    client: aiohttp.ClientSession,
    api_url: str,
    post_timeout_s: float,
    queue: asyncio.Queue,
    outbox: Outbox,
):
    # Flush at 50 new readings or 1s after the oldest unflushed one, whichever
    # comes first. Measuring from the oldest item (not the newest) keeps status
    # changes from waiting several seconds behind a busy queue.
    MAX_BATCH = 50
    MAX_WAIT_S = 1.0
    MAX_PER_REQUEST = 500

    base_api = get_base_api(api_url)
    batch_url = f"{base_api}/telemetry/batch"
    deadline = None
    new_since_flush = 0
    retry_at = 0.0
    backoff = 1.0

    async def flush():
        nonlocal deadline, new_since_flush, retry_at, backoff
        deadline = None
        new_since_flush = 0
        if time.monotonic() < retry_at:
            return

        unsent = outbox.unsent()
        # A device key only covers its own robot, so with device keys the
        # readings go out as one request per robot.
        if _device_keys:
            groups: dict[int, list] = {}
            for item in unsent:
                groups.setdefault(item["robot_id"], []).append(item)
            requests = [(items, key_headers(rid)) for rid, items in groups.items()]
        else:
            requests = [(unsent, None)]

        for items, headers in requests:
            for start in range(0, len(items), MAX_PER_REQUEST):
                chunk = items[start : start + MAX_PER_REQUEST]
                try:
                    await post_telemetry(client, batch_url, chunk, post_timeout_s, headers=headers)
                except PermanentRejection as exc:
                    safe_print(f"[BATCHER] Readings rejected, dropping {len(chunk)}: {exc}")
                    outbox.discard(chunk)
                    continue
                except Exception as exc:
                    # Kept in the outbox and retried with backoff.
                    safe_print(f"[BATCHER] POST failed, will retry: {exc!r}")
                    retry_at = time.monotonic() + backoff
                    backoff = min(backoff * 2, 30.0)
                    return
                outbox.confirm(chunk)
        backoff = 1.0

    while True:
        timeout = MAX_WAIT_S if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=timeout)
            outbox.add(payload)
            new_since_flush += 1
            if deadline is None:
                deadline = time.monotonic() + MAX_WAIT_S
            if new_since_flush >= MAX_BATCH:
                await flush()
        except asyncio.TimeoutError:
            if outbox.pending_count():
                await flush()
        except asyncio.CancelledError:
            break


def _config_applier(robot: RobotState):
    """on_config callback: the server's settings take effect on the next tick."""

    def apply(values: dict) -> None:
        robot.report_interval_s = values.get("report_interval_s")
        safe_print(f"[R{robot.robot_id:02d}] Settings applied: {values}")

    return apply


async def main_async(args, worker_index=0, total_workers=1):
    # Offset per process, or every process would start its robots with the
    # same health and battery as the first one's. Process 0 is unchanged.
    rng = random.Random(args.seed + worker_index)

    robots_count = args.robots
    if robots_count <= 0:
        # From the shared seed: every process must agree on the fleet size.
        robots_count = random.Random(args.seed).randint(35, 55)

    # Calculate chunk for this worker
    chunk_size = robots_count // total_workers
    remainder = robots_count % total_workers
    my_robots = chunk_size + (1 if worker_index < remainder else 0)

    start_id = (
        sum(chunk_size + (1 if i < remainder else 0) for i in range(worker_index)) + args.first_id
    )
    end_id = start_id + my_robots

    safe_print(
        f"[Worker {worker_index}] Initializing fleet with {my_robots} robots (IDs {start_id} to {end_id - 1})."
    )

    robots = []
    for rid in range(start_id, end_id):
        start_x, start_y = random_point(rng, args.radius * 0.2)
        battery_h, motor_h, sensor_h, network_h = initial_component_health(rng)
        robots.append(
            RobotState(
                robot_id=rid,
                battery=clamp(100.0 - rng.uniform(0, 12), 65.0, 100.0),
                temperature=clamp(args.ambient + rng.uniform(1.0, 6.0), 24.0, 50.0),
                battery_health=battery_h,
                motor_health=motor_h,
                sensor_health=sensor_h,
                network_health=network_h,
                x=start_x,
                y=start_y,
                home_x=0.0,
                home_y=0.0,
            )
        )

    mission_queue: list[Mission] = []

    # Start dispatcher loop
    dispatcher_task = asyncio.create_task(
        dispatcher_loop(
            robots=robots,
            queue=mission_queue,
            rng=random.Random(args.seed + 999),
            radius=args.radius,
        )
    )

    # `docker stop` sends SIGTERM: shut down cleanly so robots say offline
    # themselves and the outbox counts are printed.
    with contextlib.suppress(NotImplementedError, RuntimeError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)

    active_robot_tasks = {}
    headers = {"X-API-Key": args.api_key}
    connector = aiohttp.TCPConnector(limit=5000)
    telemetry_queue = asyncio.Queue()
    timeout_obj = aiohttp.ClientTimeout(total=args.timeout)
    async with aiohttp.ClientSession(
        headers=headers, connector=connector, timeout=timeout_obj
    ) as client:
        if args.device_keys or args.mqtt_host:
            await provision_device_keys(
                client, get_base_api(args.api_url), [r.robot_id for r in robots], args.timeout
            )
        if args.mqtt_host:
            from fleetops_agent.agent import Agent

            global _every_reading
            _every_reading = args.every_reading

            os.makedirs(args.outbox_dir, exist_ok=True)
            for robot in robots:
                agent = Agent(
                    robot.robot_id,
                    _device_keys[robot.robot_id],
                    args.mqtt_host,
                    args.mqtt_port,
                    outbox_path=os.path.join(args.outbox_dir, f"robot-{robot.robot_id}.db"),
                    tls=args.mqtt_tls,
                    ca_certs=args.mqtt_ca,
                )
                agent.on_config = _config_applier(robot)
                agent.start()
                _agents[robot.robot_id] = agent
            held = sum(agent.pending() for agent in _agents.values())
            safe_print(
                f"[MQTT] {len(_agents)} robots connecting to {args.mqtt_host}:{args.mqtt_port}; "
                f"{held} items held from a previous run"
            )

        # Start batcher
        outbox = Outbox()
        batcher_task = asyncio.create_task(
            telemetry_batcher(client, args.api_url, args.timeout, telemetry_queue, outbox)
        )

        # Start robot loops
        for robot in robots:
            task = asyncio.create_task(
                robot_loop(
                    robot,
                    client=client,
                    api_url=args.api_url,
                    queue=telemetry_queue,
                    rng=random.Random((args.seed * 1000) + robot.robot_id * 17),
                    ambient_c=args.ambient,
                    tick_min_s=args.tick_min,
                    tick_max_s=args.tick_max,
                    post_timeout_s=args.timeout,
                )
            )
            active_robot_tasks[robot.robot_id] = task

        try:
            while True:
                await asyncio.sleep(1.0)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            safe_print(
                f"[OUTBOX] generated={outbox.generated} unsent={outbox.pending_count()} "
                f"dropped={outbox.dropped}"
            )
            if _agents:
                # Unconfirmed items stay on disk and are sent on the next start.
                for agent in _agents.values():
                    agent.flush(timeout=2.0 / len(_agents))
                pending = sum(agent.pending() for agent in _agents.values())
                sent = sum(agent.stats["items"] for agent in _agents.values())
                observed = sum(agent.stats["observed"] for agent in _agents.values())
                resends = sum(agent.stats["resends"] for agent in _agents.values())
                messages = sum(agent.stats["messages"] for agent in _agents.values())
                sent_bytes = sum(agent.stats["bytes"] for agent in _agents.values())
                safe_print(
                    f"[MQTT] observed={observed} sent={sent} messages={messages} "
                    f"bytes={sent_bytes} unconfirmed={pending} resends={resends}"
                )
                reasons: dict[str, int] = {}
                for agent in _agents.values():
                    for key, count in agent.stats.items():
                        if key.startswith("keyframe:"):
                            reasons[key[9:]] = reasons.get(key[9:], 0) + count
                if reasons:
                    safe_print(f"[MQTT] keyframes by reason: {reasons}")
                for agent in _agents.values():
                    agent.stop()
            dispatcher_task.cancel()
            batcher_task.cancel()
            for task in list(active_robot_tasks.values()):
                task.cancel()


def worker_process(args, worker_index, total_workers):
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async(args, worker_index, total_workers))


def main():
    parser = argparse.ArgumentParser(description="Mission-based robot fleet simulator")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000/api/v1/telemetry",
    )
    parser.add_argument("--local", action="store_true", help="Use local API URL")
    parser.add_argument("--robots", type=int, default=0)
    parser.add_argument(
        "--first-id",
        type=int,
        default=1,
        help="Number the robots from here (the load test uses its own range).",
    )
    parser.add_argument("--workers", type=int, default=1)
    # Read from the environment, never a flag. A default here was a working key
    # published in a public repo, and passing one on the command line puts it in
    # `ps aux` and `docker inspect` for anyone with host access.
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TELEMETRY_API_KEY"),
        help="API key. Prefer the TELEMETRY_API_KEY environment variable.",
    )
    parser.add_argument("--ambient", type=float, default=30.0)
    parser.add_argument("--radius", type=float, default=20.0)
    # Each tick moves a robot by at most 2.5 seconds' worth (robot_loop), so a
    # longer sleep made it cover less ground than the time that passed and its
    # apparent speed jump from tick to tick. Ticks are sensor readings; with
    # dual prediction only some of them are sent.
    parser.add_argument("--tick-min", type=float, default=1.0)
    parser.add_argument("--tick-max", type=float, default=2.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help="Run battery drain, charging and wear this many times faster (default 1).",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Log every robot's state on every tick."
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--mqtt-host",
        default=None,
        help="Send over MQTT to this broker (implies --device-keys). HTTP is used to issue keys.",
    )
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-tls", action="store_true", help="Connect to the broker with TLS.")
    parser.add_argument(
        "--mqtt-ca", default=None, help="CA file to verify the broker (implies TLS)."
    )
    parser.add_argument(
        "--every-reading",
        action="store_true",
        help="With --mqtt-host, send every reading instead of only predictor keyframes.",
    )
    parser.add_argument(
        "--outbox-dir",
        default="outbox",
        help="Where each robot keeps its on-disk outbox (with --mqtt-host).",
    )
    parser.add_argument(
        "--device-keys",
        action="store_true",
        help="Issue each robot its own device key at startup and send as that robot.",
    )
    args = parser.parse_args()
    global TIME_SCALE
    TIME_SCALE = args.time_scale
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.api_key:
        parser.error(
            "No API key. Set TELEMETRY_API_KEY in the environment "
            "(the compose file already passes backend/.env through)."
        )

    if args.local:
        args.api_url = "http://localhost:8000/api/v1/telemetry"

    if args.mqtt_host and args.robots > ROBOTS_PER_PROCESS * args.workers:
        needed = -(-args.robots // ROBOTS_PER_PROCESS)
        safe_print(
            f"Running {args.robots} robots in {needed} processes instead of {args.workers} "
            f"(at most {ROBOTS_PER_PROCESS} per process over MQTT)."
        )
        args.workers = needed

    if args.workers > 1:
        processes = []
        for i in range(args.workers):
            p = multiprocessing.Process(target=worker_process, args=(args, i, args.workers))
            p.start()
            processes.append(p)

        try:
            for p in processes:
                p.join()
        except KeyboardInterrupt:
            safe_print("\nSimulator stopped.")
            for p in processes:
                p.terminate()
    else:
        try:
            asyncio.run(main_async(args, 0, 1))
        except KeyboardInterrupt:
            safe_print("\nSimulator stopped.")


if __name__ == "__main__":
    main()
