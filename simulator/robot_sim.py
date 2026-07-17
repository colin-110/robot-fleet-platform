import argparse
import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx


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


def clamp(value: float, lo: float, hi: float):
    return max(lo, min(hi, value))


def lerp(a: float, b: float, t: float):
    return a + (b - a) * t


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
        MissionStep(*random_point(rng, radius * 0.9), label=f"Inspection {index + 1}", pause_s=rng.uniform(2.0, 4.0))
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
    }.get(mission_type, 1.0)
    degraded_cap = base * clamp(robot.motor_health / 100.0, 0.45, 1.0)
    return clamp(degraded_cap + rng.uniform(-0.08, 0.12), 0.2, 2.2)


def apply_sensor_noise(robot: RobotState, value: float, *, kind: str, rng: random.Random):
    noise_scale = (100.0 - robot.sensor_health) / 100.0
    if noise_scale <= 0:
        return value

    if kind == "temperature":
        value += rng.uniform(-2.5, 2.5) * noise_scale
    elif kind == "speed":
        value += rng.uniform(-0.18, 0.18) * noise_scale
    else:
        value += rng.uniform(-1.8, 1.8) * noise_scale

    if rng.random() < noise_scale * 0.05:
        value += rng.uniform(-6.0, 6.0)

    return value


async def post_telemetry(client: httpx.AsyncClient, api_url: str, payload: dict, timeout_s: float):
    for attempt in range(3):
        try:
            response = await client.post(api_url, json=payload, timeout=timeout_s)
            response.raise_for_status()
            return
        except httpx.RequestError as exc:
            if attempt == 2:
                raise exc
            await asyncio.sleep(0.5 * (2 ** attempt))


async def dispatcher_loop(*, robots: list[RobotState], queue: list[Mission], rng: random.Random, radius: float):
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
                and robot.status not in {"DEAD", "CHARGING"}
                and robot.mission is None
                and robot.battery > 15.0
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


async def robot_loop(
    robot: RobotState,
    *,
    client: httpx.AsyncClient,
    api_url: str,
    rng: random.Random,
    ambient_c: float,
    tick_min_s: float,
    tick_max_s: float,
    post_timeout_s: float,
):
    robot.last_update_s = time.time()

    while True:
        now = time.time()
        dt = clamp(now - robot.last_update_s, 0.1, 2.5)
        robot.last_update_s = now
        
        # Poll for commands
        try:
            cmd_url = api_url.replace("/telemetry", f"/commands/{robot.robot_id}")
            cmd_resp = await client.get(cmd_url, timeout=1.0)
            if cmd_resp.status_code == 200:
                for cmd in cmd_resp.json():
                    if cmd == "RETURN_TO_BASE":
                        clear_mission(robot)
                        robot.mission = Mission(
                            mission_id="CMD-RTB",
                            mission_type="RETURN",
                            steps=[MissionStep(0.0, 0.0, label="Return to base")],
                            home_x=0.0,
                            home_y=0.0,
                            progress_weight=100.0,
                        )
                        robot.mission_id = "CMD-RTB"
                        robot.mission_progress = 0.0
                        robot.status = "ACTIVE"
                        robot.online = True
                        safe_print(f"[R{robot.robot_id:02d}] Executing RETURN_TO_BASE")
                    elif cmd == "EMERGENCY_STOP":
                        robot.status = "STOPPED"
                        robot.speed = 0.0
                        clear_mission(robot)
                        safe_print(f"[R{robot.robot_id:02d}] EMERGENCY STOP ACTIVATED")
                    elif cmd == "RESUME":
                        robot.status = "ACTIVE"
                        robot.online = True
                        safe_print(f"[R{robot.robot_id:02d}] RESUMED")
        except Exception:
            pass
            
        if robot.status in ("DEAD", "STOPPED"):
            # Emit telemetry so it stays on dashboard
            if robot.status == "STOPPED":
                await emit_telemetry(robot, client, api_url, rng, post_timeout_s)
            await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))
            continue

        if robot.mission is None and robot.battery <= 18.0 and robot.status != "STOPPED":
            robot.status = "CHARGING"
        elif robot.status not in ("CHARGING", "STOPPED"):
            robot.status = "ACTIVE"

        if robot.status == "CHARGING":
            robot.speed = lerp(robot.speed, 0.0, 0.45)
            charge_rate = 0.24 * clamp(robot.battery_health / 100.0, 0.55, 1.0)
            robot.battery = clamp(robot.battery + charge_rate * dt * 100.0, 0.0, 100.0)
            robot.temperature -= 0.08 * max(0.0, robot.temperature - ambient_c) * dt
            if robot.battery >= 92.0:
                robot.status = "ACTIVE"
            if robot.mission is None:
                clear_mission(robot)
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
                        await emit_telemetry(robot, client, api_url, rng, post_timeout_s)
                        clear_mission(robot)
                        robot.status = "ACTIVE" if robot.battery > 18.0 else "CHARGING"
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
            robot.battery -= drain_per_s * dt * 20.0

            load_heat = 0.055 + 0.055 * robot.speed + ((100.0 - robot.motor_health) / 100.0) * 0.035
            cooling = 0.02 * max(0.0, robot.temperature - ambient_c)
            robot.temperature += (load_heat - cooling) * dt
        else:
            robot.speed = lerp(robot.speed, 0.0, 0.35)
            standby_drain = 0.0015 * (1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.25)
            robot.battery -= standby_drain * dt * 20.0
            robot.temperature -= 0.04 * (robot.temperature - ambient_c) * dt

        robot.battery_health = clamp(robot.battery_health - rng.uniform(0.0006, 0.0012), 55.0, 100.0)
        robot.motor_health = clamp(
            robot.motor_health - rng.uniform(0.0008, 0.0014) * (1.3 if robot.mission else 0.5),
            50.0,
            100.0,
        )
        robot.sensor_health = clamp(robot.sensor_health - rng.uniform(0.0005, 0.0010), 58.0, 100.0)
        robot.network_health = clamp(robot.network_health - rng.uniform(0.0006, 0.0011), 52.0, 100.0)

        if rng.random() < 0.0025:
            robot.temperature += rng.uniform(4.0, 9.0)

        # Check geofence (Restricted Zone at x=15, y=10, radius 5)
        dist_to_fence = math.hypot(robot.x - 15.0, robot.y - 10.0)
        if dist_to_fence < 5.0:
            if not getattr(robot, 'in_fence', False):
                robot.in_fence = True
                safe_print(f"[R{robot.robot_id:02d}] ENTERED RESTRICTED ZONE")
                try:
                    await client.post(f"{api_url.replace('/telemetry', '')}/events", json={
                        "robot_id": robot.robot_id,
                        "message": "Entered Restricted Zone!"
                    }, timeout=2.0)
                except Exception:
                    pass
        else:
            if getattr(robot, 'in_fence', False):
                robot.in_fence = False
                safe_print(f"[R{robot.robot_id:02d}] EXITED RESTRICTED ZONE")

        robot.battery = clamp(robot.battery, 0.0, 100.0)
        robot.temperature = clamp(robot.temperature, 22.0, 99.0)
        robot.speed = clamp(robot.speed, 0.0, 2.2)

        if robot.battery <= 5.0 or robot.temperature >= 95.0:
            robot.status = "DEAD"
            robot.online = False
            robot.speed = 0.0
            robot.mission_progress = 100.0 if robot.mission_id else None
            if not robot.dead_printed:
                safe_print(f"[R{robot.robot_id:02d}] DEAD")
                robot.dead_printed = True
            
            # Emit telemetry so it doesn't disappear from the dashboard
            await emit_telemetry(robot, client, api_url, rng, post_timeout_s)
            clear_mission(robot)
            
            # Revive after a while
            if rng.random() < 0.2:  # ~20% chance per tick to revive (approx 10-25 seconds)
                robot.battery = 100.0
                robot.temperature = ambient_c
                robot.battery_health = 100.0
                robot.motor_health = 100.0
                robot.sensor_health = 100.0
                robot.network_health = 100.0
                robot.status = "ACTIVE"
                robot.online = True
                robot.dead_printed = False
                safe_print(f"[R{robot.robot_id:02d}] REVIVED and REPAIRED")
            
            await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))
            continue

        if robot.network_health < 68.0 and rng.random() < ((68.0 - robot.network_health) / 100.0):
            safe_print(f"[R{robot.robot_id:02d}] telemetry dropped (network instability)")
        else:
            await emit_telemetry(robot, client, api_url, rng, post_timeout_s)

        await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))


async def emit_telemetry(robot: RobotState, client: httpx.AsyncClient, api_url: str, rng: random.Random, post_timeout_s: float):
    payload = {
        "robot_id": robot.robot_id,
        "battery": round(clamp(apply_sensor_noise(robot, robot.battery, kind="battery", rng=rng), 0.0, 100.0), 2),
        "temperature": round(clamp(apply_sensor_noise(robot, robot.temperature, kind="temperature", rng=rng), 0.0, 120.0), 2),
        "speed": round(clamp(apply_sensor_noise(robot, robot.speed, kind="speed", rng=rng), 0.0, 3.0), 2),
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
        await post_telemetry(client, api_url, payload, post_timeout_s)
        mission_label = robot.mission.mission_type if robot.mission else "IDLE"
        progress = f"{robot.mission_progress:5.1f}%" if robot.mission_progress is not None else "  n/a"
        safe_print(
            f"[R{robot.robot_id:02d}] {robot.status:<10} {mission_label:<10} "
            f"bat={robot.battery:5.1f}% temp={robot.temperature:5.1f}C spd={robot.speed:4.2f} "
            f"mission={progress} comp=({robot.battery_health:4.0f}/{robot.motor_health:4.0f}/"
            f"{robot.sensor_health:4.0f}/{robot.network_health:4.0f}) pos=({robot.x:5.1f},{robot.y:5.1f})"
        )
    except Exception as exc:
        safe_print(f"[R{robot.robot_id:02d}] POST failed: {exc}")


async def main_async():
    parser = argparse.ArgumentParser(description="Mission-based robot fleet simulator")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000/api/v1/telemetry",
    )
    parser.add_argument("--local", action="store_true", help="Use local API URL")
    parser.add_argument("--robots", type=int, default=5)
    parser.add_argument("--ambient", type=float, default=30.0)
    parser.add_argument("--radius", type=float, default=20.0)
    parser.add_argument("--tick-min", type=float, default=2.0)
    parser.add_argument("--tick-max", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    if args.local:
        args.api_url = "http://localhost:8000/api/v1/telemetry"

    rng = random.Random(args.seed)
    robots = []
    for rid in range(1, args.robots + 1):
        start_x, start_y = random_point(rng, args.radius * 0.2)
        robots.append(
            RobotState(
                robot_id=rid,
                battery=clamp(100.0 - rng.uniform(0, 12), 65.0, 100.0),
                temperature=clamp(args.ambient + rng.uniform(1.0, 6.0), 24.0, 50.0),
                x=start_x,
                y=start_y,
                home_x=0.0,
                home_y=0.0,
            )
        )

    mission_queue: list[Mission] = []
    tasks = [
        asyncio.create_task(
            dispatcher_loop(
                robots=robots,
                queue=mission_queue,
                rng=random.Random(args.seed + 999),
                radius=args.radius,
            )
        )
    ]

    async with httpx.AsyncClient() as client:
        for robot in robots:
            tasks.append(
                asyncio.create_task(
                    robot_loop(
                        robot,
                        client=client,
                        api_url=args.api_url,
                        rng=random.Random((args.seed * 1000) + robot.robot_id * 17),
                        ambient_c=args.ambient,
                        tick_min_s=args.tick_min,
                        tick_max_s=args.tick_max,
                        post_timeout_s=args.timeout,
                    )
                )
            )

        await asyncio.gather(*tasks)


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        safe_print("\nSimulator stopped.")


if __name__ == "__main__":
    main()
