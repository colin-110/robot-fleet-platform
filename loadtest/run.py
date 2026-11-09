"""Load test the local stack with N simulated robots and report where it strains.

Runs against the stack started with ``docker compose up``. It stops the
regular simulator, starts one with ``--robots N`` (each robot on its own
agent, as in normal use), waits for the robots to come online, then samples
the API's ``/metrics`` and ``docker stats`` for every container. At the end it
puts the regular simulator back and prints a report::

    python3 loadtest/run.py --robots 500 --minutes 5
    python3 loadtest/run.py --robots 2000 --minutes 5 --json out.json

Only the standard library is needed. The simulator's own CPU is reported
separately, so a saturated simulator is not mistaken for a slow server.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOAD_CONTAINER = "fleetops-load"
SERVICES = ("backend", "db", "mqtt")
SAMPLE_SECONDS = 10


def sh(*cmd: str, check: bool = True) -> str:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        sys.exit(f"{' '.join(cmd)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def env_value(name: str, default: str | None = None) -> str:
    for line in (ROOT / "backend/.env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"')
    if default is None:
        sys.exit(f"{name} is not set in backend/.env")
    return default


# ── /metrics ────────────────────────────────────────────────────────

SAMPLE = re.compile(r"^([a-z_]+)(\{[^}]*\})?\s+(\S+)$")


def scrape(url: str, key: str) -> dict[str, float]:
    request = urllib.request.Request(url, headers={"X-API-Key": key})
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode()
    values = {}
    for line in text.splitlines():
        match = SAMPLE.match(line)
        if match:
            name, labels, value = match.groups()
            values[name + (labels or "")] = float(value)
    return values


def buckets(metrics: dict[str, float], name: str) -> list[tuple[float, float]]:
    """A histogram's cumulative buckets as (upper bound, count), sorted."""
    out = []
    for key, value in metrics.items():
        match = re.fullmatch(rf'{name}_bucket\{{le="([^"]+)"\}}', key)
        if match:
            out.append((float(match.group(1)), value))
    return sorted(out)


def quantile(start: dict, end: dict, name: str, q: float) -> float | None:
    """The ``q`` quantile of what a histogram recorded between two scrapes
    (the upper bound of the bucket it falls in)."""
    before = dict(buckets(start, name))
    deltas = [(le, count - before.get(le, 0.0)) for le, count in buckets(end, name)]
    total = deltas[-1][1] if deltas else 0
    if total <= 0:
        return None
    for le, count in deltas:
        if count >= q * total:
            return le
    return float("inf")


def delta(start: dict, end: dict, name: str) -> float:
    return end.get(name, 0.0) - start.get(name, 0.0)


# ── docker stats ────────────────────────────────────────────────────

UNITS = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "kB": 1e3, "MB": 1e6, "GB": 1e9}


def to_bytes(text: str) -> float:
    match = re.match(r"([\d.]+)\s*([A-Za-z]+)", text)
    return float(match.group(1)) * UNITS.get(match.group(2), 1) if match else 0.0


def container_ids() -> dict[str, str]:
    ids = {s: sh("docker", "compose", "ps", "-q", s) for s in SERVICES}
    missing = [s for s, cid in ids.items() if not cid]
    if missing:
        sys.exit(f"Not running: {', '.join(missing)}. Start the stack with `docker compose up -d`.")
    ids["simulator"] = LOAD_CONTAINER
    return ids


def docker_stats(ids: dict[str, str]) -> dict[str, tuple[float, float]]:
    """Per service: (CPU %, memory bytes). 100 % is one core."""
    out = sh("docker", "stats", "--no-stream", "--format", "{{json .}}", *ids.values(), check=False)
    by_id = {}
    for line in out.splitlines():
        row = json.loads(line)
        by_id[row["Name"]] = by_id[row["ID"]] = (
            float(row["CPUPerc"].rstrip("%") or 0),
            to_bytes(row["MemUsage"].split("/")[0]),
        )
    return {
        service: by_id[cid] if cid in by_id else by_id.get(cid[:12], (0.0, 0.0))
        for service, cid in ids.items()
    }


def db_bytes() -> float:
    user, name = env_value("POSTGRES_USER"), env_value("POSTGRES_DB")
    query = f"select pg_database_size('{name}')"
    out = sh("docker", "compose", "exec", "-T", "db", "psql", "-U", user, "-d", name, "-Atc", query)
    return float(out)


# ── the run ─────────────────────────────────────────────────────────


def start_load(robots: int, workers: int, time_scale: float) -> None:
    sh("docker", "rm", "-f", LOAD_CONTAINER, check=False)
    sh("docker", "compose", "stop", "simulator")
    sh(
        "docker", "compose", "run", "-d", "--name", LOAD_CONTAINER, "simulator",
        "python", "-u", "robot_sim.py",
        "--api-url", "http://backend:8000/api/v1/telemetry",
        "--mqtt-host", "mqtt", "--outbox-dir", "/data",
        "--robots", str(robots), "--workers", str(workers),
        "--time-scale", str(time_scale),
    )  # fmt: skip


def stop_load(restore: bool) -> None:
    sh("docker", "stop", "-t", "20", LOAD_CONTAINER, check=False)
    sh("docker", "rm", "-f", LOAD_CONTAINER, check=False)
    if restore:
        sh("docker", "compose", "start", "simulator", check=False)


def check_load_running() -> None:
    """Stop with the simulator's last lines if it has died."""
    state = sh("docker", "inspect", "-f", "{{.State.Running}}", LOAD_CONTAINER, check=False)
    if state != "true":
        logs = subprocess.run(
            ["docker", "logs", "--tail", "15", LOAD_CONTAINER], capture_output=True, text=True
        )
        raise SystemExit(f"\nThe load simulator stopped:\n{(logs.stdout + logs.stderr).strip()}")


def wait_online(url: str, key: str, robots: int, limit: float) -> float:
    started = time.time()
    online = 0.0
    while time.time() - started < limit:
        check_load_running()
        with contextlib.suppress(OSError):
            online = scrape(url, key).get("fleetops_robots_online", 0.0)
        print(f"\r  {online:.0f}/{robots} robots online", end="", flush=True)
        if online >= 0.95 * robots:
            break
        time.sleep(5)
    print()
    return time.time() - started


def summarise(samples, stats, start, end, seconds, db_growth, robots, batch_limit):
    def mean(values):
        return sum(values) / len(values) if values else 0.0

    batches = delta(start, end, "fleetops_ingest_batches_total")
    messages = delta(start, end, "fleetops_ingest_batch_messages_sum")
    batch_seconds = delta(start, end, "fleetops_ingest_batch_seconds_sum")
    alarm_passes = delta(start, end, "fleetops_alarm_pass_seconds_count")
    report = {
        "robots": robots,
        "seconds": round(seconds),
        "robots_online": end.get("fleetops_robots_online", 0.0),
        "readings_per_s": delta(start, end, "fleetops_ingest_stored_total") / seconds,
        "messages_per_s": messages / seconds,
        "duplicates": delta(start, end, "fleetops_ingest_duplicates_total"),
        "rejected": delta(start, end, "fleetops_ingest_rejected_total"),
        "batch_ms_mean": 1000 * batch_seconds / batches if batches else None,
        "batch_ms_p95": (lambda v: v and 1000 * v)(
            quantile(start, end, "fleetops_ingest_batch_seconds", 0.95)
        ),
        "messages_per_batch": messages / batches if batches else None,
        "batch_full_pct": 100 * messages / batches / batch_limit if batches else None,
        # Share of wall time ingest spent processing batches: near 100 % is saturated.
        "ingest_busy_pct": 100 * batch_seconds / seconds,
        "queue_depth_max": max(s.get("fleetops_ingest_queue_depth", 0.0) for s in samples),
        "reading_age_p50_s": quantile(start, end, "fleetops_reading_age_seconds", 0.5),
        "reading_age_p95_s": quantile(start, end, "fleetops_reading_age_seconds", 0.95),
        "alarm_pass_ms_mean": (
            1000 * delta(start, end, "fleetops_alarm_pass_seconds_sum") / alarm_passes
            if alarm_passes
            else None
        ),
        "ws_frames_sent_per_s": delta(start, end, "fleetops_websocket_frames_sent_total") / seconds,
        "ws_frames_dropped": delta(start, end, "fleetops_websocket_frames_dropped_total"),
        "db_growth_mb_per_hour": db_growth / 2**20 * 3600 / seconds,
        "containers": {
            service: {
                "cpu_pct_mean": mean([s[service][0] for s in stats]),
                "cpu_pct_max": max(s[service][0] for s in stats),
                "mem_mb_max": max(s[service][1] for s in stats) / 2**20,
            }
            for service in stats[0]
        },
    }
    return report


def fmt(value, digits=1):
    if value is None:
        return "-"
    if isinstance(value, float) and value == float("inf"):
        return "over the top bucket"
    return f"{value:,.{digits}f}"


def print_report(r: dict) -> None:
    print(f"\n## {r['robots']} robots, {r['seconds']} s ({fmt(r['robots_online'], 0)} online)\n")
    rows = [
        ("Readings stored / s", fmt(r["readings_per_s"])),
        ("MQTT messages / s", fmt(r["messages_per_s"])),
        ("Messages per batch (full %)", f"{fmt(r['messages_per_batch'])} ({fmt(r['batch_full_pct'], 0)} %)"),
        ("Batch time mean / p95 (ms)", f"{fmt(r['batch_ms_mean'])} / {fmt(r['batch_ms_p95'], 0)}"),
        ("Ingest busy", f"{fmt(r['ingest_busy_pct'], 0)} %"),
        ("Ingest queue depth, max", fmt(r["queue_depth_max"], 0)),
        ("Reading age p50 / p95 (s)", f"{fmt(r['reading_age_p50_s'], 2)} / {fmt(r['reading_age_p95_s'], 2)}"),
        ("Duplicates / rejected", f"{fmt(r['duplicates'], 0)} / {fmt(r['rejected'], 0)}"),
        ("Alarm pass mean (ms)", fmt(r["alarm_pass_ms_mean"], 2)),
        ("WebSocket frames / s, dropped", f"{fmt(r['ws_frames_sent_per_s'])}, {fmt(r['ws_frames_dropped'], 0)}"),
        ("Database growth (MB / hour)", fmt(r["db_growth_mb_per_hour"])),
    ]  # fmt: skip
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"  {name.ljust(width)}  {value}")
    print(f"\n  {'Container'.ljust(10)}  {'CPU mean':>9}  {'CPU max':>8}  {'Memory max':>11}")
    for service, c in r["containers"].items():
        print(
            f"  {service.ljust(10)}  {c['cpu_pct_mean']:>8.0f}%  {c['cpu_pct_max']:>7.0f}%"
            f"  {c['mem_mb_max']:>8.0f} MB"
        )
    print("\n  100 % CPU is one core. The API runs one process, so near 100 % it is the limit.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--robots", type=int, required=True)
    parser.add_argument(
        "--workers", type=int, default=0, help="Simulator processes (the simulator adds more as it needs them)."
    )
    parser.add_argument(
        "--minutes", type=float, default=5.0, help="How long to measure, after warm-up."
    )
    parser.add_argument(
        "--warmup-limit",
        type=float,
        default=300.0,
        help="Longest wait for robots to come online (s).",
    )
    parser.add_argument("--time-scale", type=float, default=1.0, help="Passed to the simulator.")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--json", help="Also write the report here, to compare runs.")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave the load running and do not restore the regular simulator.",
    )
    args = parser.parse_args()  # fmt: skip

    # The simulator raises this itself to keep each process under its limit.
    workers = args.workers or 1
    key = env_value("TELEMETRY_API_KEY")
    url = f"{args.api}/metrics"
    batch_limit = int(env_value("MQTT_BATCH_SIZE", "200"))

    ids = container_ids()
    print(f"Starting {args.robots} robots")
    start_load(args.robots, workers, args.time_scale)
    try:
        warm = wait_online(url, key, args.robots, args.warmup_limit)
        print(f"  warm-up took {warm:.0f} s; measuring for {args.minutes:g} min")
        # Let the reconnect burst settle before measuring.
        time.sleep(SAMPLE_SECONDS)
        start, db_start, began = scrape(url, key), db_bytes(), time.time()
        samples, stats = [start], []
        while time.time() - began < args.minutes * 60:
            time.sleep(SAMPLE_SECONDS)
            check_load_running()
            samples.append(scrape(url, key))
            stats.append(docker_stats(ids))
            print(f"\r  {time.time() - began:.0f} s", end="", flush=True)
        print()
        end, seconds = samples[-1], time.time() - began
        report = summarise(
            samples, stats, start, end, seconds, db_bytes() - db_start, args.robots, batch_limit
        )
    finally:
        if not args.keep:
            stop_load(restore=True)
    print_report(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
        print(f"\n  Written to {args.json}")


if __name__ == "__main__":
    main()
