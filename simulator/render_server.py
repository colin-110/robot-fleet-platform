"""
Render entrypoint: wraps robot_sim.py with a dummy HTTP server.

Render's free tier only keeps non-web background processes alive on a paid
plan. The simulator has nothing to serve, so this opens a trivial health port
next to it purely so Render (and an external uptime pinger) can treat it as a
normal free web service and keep it running continuously.
"""

import asyncio
import os
import sys
import time

from aiohttp import web

SIMULATOR_PROC: asyncio.subprocess.Process | None = None

# robot_sim.py touches this file (see post_telemetry) on every telemetry POST
# that actually succeeds. Checking only "is the subprocess still alive" missed
# a real incident: the subprocess hung without exiting, so Render's restart
# policy — which only fires from run_simulator()'s os._exit() below — never
# triggered, and the health port kept reporting "ok" for 7+ hours with zero
# telemetry actually reaching the backend.
HEARTBEAT_FILE = "/tmp/simulator_healthy"
START_TIME = time.time()
STARTUP_GRACE_SECONDS = 60  # first successful batch needs time to land
HEARTBEAT_STALE_SECONDS = 90  # generous vs. the simulator's own tick/batch cadence


async def health(_request: web.Request) -> web.Response:
    if time.time() - START_TIME < STARTUP_GRACE_SECONDS:
        return web.Response(text="ok (starting)")

    try:
        age = time.time() - os.path.getmtime(HEARTBEAT_FILE)
    except OSError:
        return web.Response(status=503, text="no successful telemetry post yet")

    if age > HEARTBEAT_STALE_SECONDS:
        return web.Response(status=503, text=f"heartbeat stale ({age:.0f}s)")

    return web.Response(text="ok")


async def run_simulator() -> None:
    global SIMULATOR_PROC
    api_url = os.environ.get("SIMULATOR_API_URL", "http://localhost:8000/api/v1/telemetry")
    robots = os.environ.get("SIMULATOR_ROBOTS", "24")
    workers = os.environ.get("SIMULATOR_WORKERS", "2")

    SIMULATOR_PROC = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "robot_sim.py",
        "--api-url", api_url,
        "--robots", robots,
        "--workers", workers,
    )
    returncode = await SIMULATOR_PROC.wait()
    # The simulator died; exit so Render's restart policy brings the whole
    # service (and the subprocess with it) back up rather than leaving a
    # health port that reports healthy with no simulator behind it.
    os._exit(returncode or 1)


async def main() -> None:
    port = int(os.environ.get("PORT", "8080"))

    app = web.Application()
    app.add_routes([web.get("/", health), web.get("/health", health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    asyncio.create_task(run_simulator())
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
