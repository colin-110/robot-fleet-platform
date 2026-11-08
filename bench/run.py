"""FleetOps benchmark: the whole stack under 512 MB, measured.

    python3 bench/run.py [--minutes 10] [--robots 24] [--keep]

Starts bench/docker-compose.yml, lets the simulator run, samples every
container's memory and CPU, then reports messages and bytes per robot, the
database's growth per robot per day (raw and compressed), any out-of-memory
kills, and the cost per robot per month on the instances it would fit. Writes
the report to bench/results.md and the raw numbers to bench/results.json.

Needs Docker with Compose. Uses ports 18100 (API) on localhost.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "docker-compose.yml")]
SERVICES = ("db", "mqtt", "backend", "simulator")
LIMIT_MB = {"db": 200, "mqtt": 48, "backend": 160, "simulator": 72}

# On-demand Linux prices, us-east-1, per month (730 h), plus what any instance
# needs: a public IPv4 address and a gp3 volume. Check before relying on them.
INSTANCES = {
    "t4g.nano (0.5 GB)": 0.0042 * 730,
    "t4g.micro (1 GB)": 0.0084 * 730,
    "t4g.small (2 GB)": 0.0168 * 730,
}
IPV4 = 0.005 * 730
GP3_PER_GB = 0.08
# What scripts/deploy_aws.ps1 provisions: 30 days of keyframes plus rollups.
DISK_GB = 16


def sh(*args: str, check: bool = True) -> str:
    return subprocess.run(args, check=check, capture_output=True, text=True).stdout


def compose(*args: str, check: bool = True) -> str:
    return sh(*COMPOSE, *args, check=check)


def psql(sql: str) -> str:
    return compose("exec", "-T", "db", "psql", "-U", "fleet", "-d", "fleet", "-tAc", sql).strip()


_ids: dict[str, str] = {}


def container(service: str) -> str:
    """The service's container id, looked up once (a stopped one is not listed)."""
    if service not in _ids:
        _ids[service] = compose("ps", "-a", "-q", service).strip()
    return _ids[service]


def to_mb(text: str) -> float:
    number, unit = re.match(r"([\d.]+)\s*([KMG]i?B)", text).groups()
    return (
        float(number)
        * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "KB": 1 / 1000, "MB": 1, "GB": 1000}[unit]
    )


def sample() -> dict[str, tuple[float, float]]:
    """Memory (MB) and CPU (% of one core) per service, right now."""
    ids = {container(s): s for s in SERVICES}
    out = sh(
        "docker", "stats", "--no-stream", "--format", "{{.ID}} {{.MemUsage}} {{.CPUPerc}}", *ids
    )
    result = {}
    for line in out.splitlines():
        # "<id> 45.2MiB / 200MiB 1.23%"
        parts = line.split()
        name = next(s for full, s in ids.items() if full.startswith(parts[0]))
        result[name] = (to_mb(parts[1]), float(parts[-1].rstrip("%")))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--robots", type=int, default=24)
    parser.add_argument("--keep", action="store_true", help="leave the stack running")
    args = parser.parse_args()

    print("Building and starting the stack...", flush=True)
    compose("down", "-v", check=False)
    subprocess.run(
        [*COMPOSE, "up", "-d", "--build", "--wait"],
        check=True,
        env={**os.environ, "ROBOTS": str(args.robots)},
    )
    print("Warming up for 60 s...", flush=True)
    time.sleep(60)

    rows0 = int(psql("SELECT count(*) FROM telemetry"))
    size0 = int(psql("SELECT hypertable_size('telemetry')"))
    t0 = time.time()
    samples: dict[str, list[tuple[float, float]]] = {s: [] for s in SERVICES}
    deadline = t0 + args.minutes * 60
    while time.time() < deadline:
        for service, value in sample().items():
            samples[service].append(value)
        print(
            f"  {int(time.time() - t0):>4}s  "
            + "  ".join(f"{s} {v[-1][0]:.0f}MB" for s, v in samples.items() if v),
            flush=True,
        )
        time.sleep(10)
    elapsed = time.time() - t0
    rows1 = int(psql("SELECT count(*) FROM telemetry"))
    size1 = int(psql("SELECT hypertable_size('telemetry')"))

    # Compress everything so far to see what a day costs once compressed.
    psql(
        "SELECT count(compress_chunk(c, if_not_compressed => true)) FROM show_chunks('telemetry') c"
    )
    compressed = int(psql("SELECT hypertable_size('telemetry')"))
    raw_total = int(psql("SELECT pg_database_size('fleet')"))

    compose("stop", "simulator")
    sim_log = compose("logs", "simulator")
    oom = {
        s: json.loads(sh("docker", "inspect", container(s)) or "[{}]")[0]
        .get("State", {})
        .get("OOMKilled", False)
        for s in SERVICES
    }
    m = re.search(r"observed=(\d+) sent=(\d+) messages=(\d+) bytes=(\d+)", sim_log)
    observed, sent, messages, sent_bytes = (int(x) for x in m.groups()) if m else (0, 0, 0, 0)
    sim_seconds = elapsed + 60  # the agent counters include the warm-up

    robots = args.robots
    keyframes = rows1 - rows0
    per_robot_min = keyframes / robots / (elapsed / 60)
    db_raw_day = (size1 - size0) / robots / elapsed * 86400
    db_compressed_day = db_raw_day * compressed / max(size1, 1)
    peak = {s: max(v[0] for v in vals) for s, vals in samples.items()}
    mean = {s: statistics.mean(v[0] for v in vals) for s, vals in samples.items()}
    cpu = {s: statistics.mean(v[1] for v in vals) for s, vals in samples.items()}
    total_peak = sum(peak.values())
    disk_cost = GP3_PER_GB * DISK_GB

    result = {
        "robots": robots,
        "minutes": round(elapsed / 60, 1),
        "keyframes_per_robot_per_min": round(per_robot_min, 2),
        "readings_observed_per_robot_per_min": round(observed / robots / (sim_seconds / 60), 2),
        "uplink_messages_per_robot_per_min": round(messages / robots / (sim_seconds / 60), 2),
        "uplink_bytes_per_robot_per_day": round(sent_bytes / robots / sim_seconds * 86400),
        "db_bytes_per_robot_per_day_raw": round(db_raw_day),
        "db_bytes_per_robot_per_day_compressed": round(db_compressed_day),
        "memory_peak_mb": {s: round(v, 1) for s, v in peak.items()},
        "memory_mean_mb": {s: round(v, 1) for s, v in mean.items()},
        "memory_limit_mb": LIMIT_MB,
        "memory_peak_total_mb": round(total_peak, 1),
        "cpu_mean_percent_of_one_core": {s: round(v, 1) for s, v in cpu.items()},
        "oom_killed": oom,
        "database_size_mb": round(raw_total / 2**20, 1),
        "cost_per_robot_per_month_usd": {
            name: round((price + IPV4 + disk_cost) / robots, 3) for name, price in INSTANCES.items()
        },
    }
    (HERE / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = [
        "# Benchmark results",
        "",
        f"{robots} simulated robots for {result['minutes']} minutes, stack limited to "
        f"{sum(LIMIT_MB.values())} MB (`bench/docker-compose.yml`). Generated by `bench/run.py`.",
        "",
        "| Measure | Value |",
        "| :--- | ---: |",
        f"| Sensor readings per robot per minute | {result['readings_observed_per_robot_per_min']} |",
        f"| Keyframes stored per robot per minute | {result['keyframes_per_robot_per_min']} |",
        f"| MQTT messages per robot per minute | {result['uplink_messages_per_robot_per_min']} |",
        f"| Uplink bytes per robot per day | {result['uplink_bytes_per_robot_per_day']:,} |",
        f"| Database growth per robot per day, raw | {result['db_bytes_per_robot_per_day_raw']:,} B |",
        f"| Database growth per robot per day, compressed | {result['db_bytes_per_robot_per_day_compressed']:,} B |",
        f"| Peak memory, all containers | {result['memory_peak_total_mb']} MB |",
        "",
        "| Container | Limit MB | Peak MB | Mean MB | CPU % (one core) | OOM killed |",
        "| :--- | ---: | ---: | ---: | ---: | :--- |",
        *(
            f"| {s} | {LIMIT_MB[s]} | {result['memory_peak_mb'][s]} | {result['memory_mean_mb'][s]} "
            f"| {result['cpu_mean_percent_of_one_core'][s]} | {oom[s]} |"
            for s in SERVICES
        ),
        "",
        f"Cost per robot per month, instance + public IPv4 + {DISK_GB} GB gp3, us-east-1 on-demand:",
        "",
        *(f"- {name}: ${v}" for name, v in result["cost_per_robot_per_month_usd"].items()),
        "",
    ]
    (HERE / "results.md").write_text("\n".join(lines))
    print("\n".join(lines))
    if not args.keep:
        compose("down", "-v", check=False)
    return 1 if any(oom.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
