import argparse
import asyncio
import math
import random
import time
from dataclasses import dataclass

import requests


@dataclass
class RobotState:

    robot_id: int

    battery: float = 100.0
    temperature: float = 33.0
    speed: float = 0.0

    mode: str = "IDLE"

    task: str = "NONE"

    online: bool = True

    health: float = 100.0

    dead_printed: bool = False

    x: float = 0.0
    y: float = 0.0

    target_x: float = 0.0
    target_y: float = 0.0

    last_update_s: float = 0.0

    moving_until_s: float = 0.0


def clamp(value: float, lo: float, hi: float):

    return max(lo, min(hi, value))


def lerp(a: float, b: float, t: float):

    return a + (b - a) * t


def pick_waypoint(rng: random.Random, radius: float):

    angle = rng.random() * math.tau

    r = radius * math.sqrt(rng.random())

    return (
        math.cos(angle) * r,
        math.sin(angle) * r
    )


def safe_print(message: str):

    try:

        print(message)

    except OSError:

        pass


async def post_telemetry(
    api_url: str,
    payload: dict,
    timeout_s: float
):

    def _post():

        requests.post(
            api_url,
            json=payload,
            timeout=timeout_s
        )

    await asyncio.to_thread(_post)


async def robot_loop(

    robot: RobotState,

    *,
    api_url: str,
    rng: random.Random,
    max_active_movers: asyncio.Semaphore,
    ambient_c: float,
    waypoint_radius_m: float,
    tick_min_s: float,
    tick_max_s: float,
    post_timeout_s: float,
):

    robot.last_update_s = time.time()

    robot.target_x, robot.target_y = pick_waypoint(
        rng,
        waypoint_radius_m
    )

    mover_token = False

    while True:

        now = time.time()

        dt = clamp(
            now - robot.last_update_s,
            0.05,
            2.0
        )

        robot.last_update_s = now

        # -----------------------------------
        # DEAD
        # -----------------------------------

        if robot.mode == "DEAD":

            await asyncio.sleep(
                rng.uniform(
                    tick_min_s,
                    tick_max_s
                )
            )

            continue

        # -----------------------------------
        # ONLINE / OFFLINE
        # -----------------------------------

        if robot.online and rng.random() < 0.001:

            robot.online = False

            safe_print(
                f"[R{robot.robot_id:02d}] OFFLINE"
            )

        if not robot.online:

            if rng.random() < 0.08:

                robot.online = True

                safe_print(
                    f"[R{robot.robot_id:02d}] RECONNECTED"
                )

            await asyncio.sleep(
                rng.uniform(1.0, 3.0)
            )

            continue

        # -----------------------------------
        # CHARGING DECISION
        # -----------------------------------

        if robot.mode == "CHARGING":

            if robot.battery >= 92:

                robot.mode = "IDLE"

        else:

            if robot.battery <= 14:

                robot.mode = "CHARGING"

            elif robot.mode == "IDLE":

                if rng.random() < 0.28:

                    await max_active_movers.acquire()

                    mover_token = True

                    robot.mode = "MOVING"

                    robot.task = rng.choice([
                        "PATROL",
                        "DELIVERY",
                        "INSPECTION"
                    ])

                    robot.moving_until_s = (
                        now + rng.uniform(8, 24)
                    )

                    robot.target_x, robot.target_y = (
                        pick_waypoint(
                            rng,
                            waypoint_radius_m
                        )
                    )

            elif robot.mode == "MOVING":

                if now >= robot.moving_until_s:

                    robot.mode = "IDLE"

                    robot.task = "NONE"

        # -----------------------------------
        # RELEASE SEMAPHORE
        # -----------------------------------

        if (
            robot.mode != "MOVING"
            and mover_token
        ):

            max_active_movers.release()

            mover_token = False

        # -----------------------------------
        # MOVING
        # -----------------------------------

        if robot.mode == "MOVING":

            if robot.task == "PATROL":

                target_speed = rng.uniform(
                    0.6,
                    1.2
                )

            elif robot.task == "DELIVERY":

                target_speed = rng.uniform(
                    1.2,
                    2.0
                )

            else:

                target_speed = rng.uniform(
                    0.8,
                    1.5
                )

            robot.speed = lerp(
                robot.speed,
                target_speed,
                0.18
            )

            dx = robot.target_x - robot.x
            dy = robot.target_y - robot.y

            dist = math.hypot(dx, dy)

            if dist < 0.6:

                robot.target_x, robot.target_y = (
                    pick_waypoint(
                        rng,
                        waypoint_radius_m
                    )
                )

            else:

                step = robot.speed * dt

                robot.x += (dx / dist) * step

                robot.y += (dy / dist) * step

            drain_per_s = (
                0.010
                + 0.012 * robot.speed
                + rng.uniform(0.0, 0.006)
            )

            robot.battery -= (
                drain_per_s * dt * 100.0
            )

            load_heat = (
                0.06
                + 0.06 * robot.speed
            )

            cooling = (
                0.025
                * max(
                    0.0,
                    robot.temperature - ambient_c
                )
            )

            robot.temperature += (
                load_heat - cooling
            ) * dt

        # -----------------------------------
        # IDLE
        # -----------------------------------

        elif robot.mode == "IDLE":

            robot.speed = lerp(
                robot.speed,
                0.0,
                0.35
            )

            drain_per_s = (
                0.0012
                + rng.uniform(
                    0.0,
                    0.0008
                )
            )

            robot.battery -= (
                drain_per_s * dt * 100.0
            )

            cooling = (
                0.040
                * (
                    robot.temperature
                    - ambient_c
                )
            )

            robot.temperature -= (
                cooling * dt
            )

        # -----------------------------------
        # CHARGING
        # -----------------------------------

        elif robot.mode == "CHARGING":

            robot.speed = lerp(
                robot.speed,
                0.0,
                0.45
            )

            base = 0.22

            taper = clamp(
                (robot.battery - 80) / 20,
                0.0,
                1.0
            )

            charge_rate = (
                base
                * (1.0 - 0.7 * taper)
            )

            robot.battery += (
                charge_rate * dt * 100.0
            )

            robot.temperature -= (
                0.08
                * (
                    robot.temperature
                    - ambient_c
                )
                * dt
            )

        # -----------------------------------
        # RANDOM FAULTS
        # -----------------------------------

        if rng.random() < 0.003:

            robot.temperature += rng.uniform(
                6,
                14
            )

        if (
            robot.mode == "MOVING"
            and rng.random() < 0.006
        ):

            robot.speed = clamp(
                robot.speed + rng.uniform(
                    -0.25,
                    0.35
                ),
                0.0,
                2.4
            )

        # -----------------------------------
        # BATTERY HEALTH DEGRADATION
        # -----------------------------------

        robot.health -= rng.uniform(
            0.00001,
            0.00008
        )

        robot.health = clamp(
            robot.health,
            70.0,
            100.0
        )

        max_battery = robot.health

        robot.battery = clamp(
            robot.battery,
            0.0,
            max_battery
        )

        robot.temperature = clamp(
            robot.temperature,
            22.0,
            98.0
        )

        robot.speed = clamp(
            robot.speed,
            0.0,
            2.6
        )

        # -----------------------------------
        # DEAD STATE
        # -----------------------------------

        if (
            robot.battery < 5.0
            or robot.temperature > 95.0
        ):

            if mover_token:

                max_active_movers.release()

                mover_token = False

            robot.mode = "DEAD"
            robot.task = "NONE"
            robot.online = False
            robot.speed = 0.0

            if not robot.dead_printed:

                safe_print(
                    f"[R{robot.robot_id:02d}] DEAD"
                )

                robot.dead_printed = True

            await asyncio.sleep(
                rng.uniform(
                    tick_min_s,
                    tick_max_s
                )
            )

            continue

        payload = {

            "robot_id": robot.robot_id,

            "battery": round(
                robot.battery,
                2
            ),

            "temperature": round(
                robot.temperature,
                2
            ),

            "speed": round(
                robot.speed,
                2
            )
        }

        try:

            await post_telemetry(
                api_url,
                payload,
                post_timeout_s
            )

            safe_print(

                f"[R{robot.robot_id:02d}] "

                f"{robot.mode:<10} "

                f"{robot.task:<12} "

                f"bat={robot.battery:5.1f}% "

                f"temp={robot.temperature:5.1f}C "

                f"spd={robot.speed:4.2f} "

                f"health={robot.health:5.1f}% "

                f"pos=({robot.x:5.1f},{robot.y:5.1f})"
            )

        except Exception as exc:

            safe_print(
                f"[R{robot.robot_id:02d}] "
                f"POST failed: {exc}"
            )

        await asyncio.sleep(
            rng.uniform(
                tick_min_s,
                tick_max_s
            )
        )


async def main_async():

    parser = argparse.ArgumentParser(
        description="Scalable AI robot fleet simulator"
    )

    parser.add_argument(
        "--api-url",
        default="https://robot-fleet-platform-production.up.railway.app/telemetry"
    )

    parser.add_argument(
        "--robots",
        type=int,
        default=5
    )

    parser.add_argument(
        "--active-movers",
        type=int,
        default=4
    )

    parser.add_argument(
        "--ambient",
        type=float,
        default=30.0
    )

    parser.add_argument(
        "--radius",
        type=float,
        default=20.0
    )

    parser.add_argument(
        "--tick-min",
        type=float,
        default=2
    )

    parser.add_argument(
        "--tick-max",
        type=float,
        default=5
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0
    )

    args = parser.parse_args()

    rng = random.Random(args.seed)

    semaphore = asyncio.Semaphore(
        max(1, args.active_movers)
    )

    robots = []

    for rid in range(1, args.robots + 1):

        robots.append(

            RobotState(

                robot_id=rid,

                battery=clamp(
                    100.0 - rng.uniform(0, 14),
                    60.0,
                    100.0
                ),

                temperature=clamp(
                    args.ambient
                    + rng.uniform(1.5, 7.5),
                    24.0,
                    60.0
                ),

                x=rng.uniform(
                    -args.radius * 0.25,
                    args.radius * 0.25
                ),

                y=rng.uniform(
                    -args.radius * 0.25,
                    args.radius * 0.25
                ),
            )
        )

    tasks = []

    for robot in robots:

        robot_rng = random.Random(
            (args.seed * 1000)
            + robot.robot_id * 17
        )

        tasks.append(

            asyncio.create_task(

                robot_loop(

                    robot,

                    api_url=args.api_url,

                    rng=robot_rng,

                    max_active_movers=semaphore,

                    ambient_c=args.ambient,

                    waypoint_radius_m=args.radius,

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

        safe_print(
            "\nSimulator stopped."
        )


if __name__ == "__main__":

    main()
