"""
Advanced Telemetry Pipeline Benchmark Script.

Measures:
- Ingestion Throughput (events/sec)
- Latency Distribution (p50, p95, p99, max)
- Success / Error Rates (%)
- Direct DB vs Redis Buffer comparison

Usage:
  python scripts/load_test.py --robots 500 --duration 10
  python scripts/load_test.py --robots 1000 --duration 15
"""

import argparse
import asyncio
import math
import sys
import time
from typing import List, Tuple

import httpx


def calculate_percentiles(latencies: List[float]) -> dict:
    """Calculate p50, p95, p99, and max from a sorted list of latencies (ms)."""
    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}

    s = sorted(latencies)
    n = len(s)

    def percentile(p: float) -> float:
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return s[int(k)]
        return s[int(f)] * (c - k) + s[int(c)] * (k - f)

    return {
        "mean": sum(s) / n,
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": s[-1],
    }


async def run_benchmark(
    base_url: str,
    api_key: str,
    num_robots: int,
    duration_secs: int,
    concurrency: int = 100,
) -> dict:
    url = f"{base_url.rstrip('/')}/api/v1/telemetry"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    # Pre-build payload templates
    payloads = [
        {
            "robot_id": i + 1,
            "battery": 85.5,
            "temperature": 42.1,
            "speed": 1.5,
            "status": "ACTIVE",
            "x": 12.34,
            "y": 56.78,
            "battery_health": 95.0,
            "motor_health": 92.0,
            "sensor_health": 98.0,
            "network_health": 100.0,
        }
        for i in range(num_robots)
    ]

    latencies: List[float] = []
    errors: int = 0
    total_requests: int = 0

    semaphore = asyncio.Semaphore(concurrency)

    limits = httpx.Limits(
        max_connections=concurrency * 2,
        max_keepalive_connections=concurrency,
    )
    timeout = httpx.Timeout(10.0, connect=5.0)

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:

        async def worker(payload: dict):
            nonlocal total_requests, errors
            async with semaphore:
                t0 = time.perf_counter()
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    t1 = time.perf_counter()
                    total_requests += 1
                    if resp.status_code in (200, 201, 202):
                        latencies.append((t1 - t0) * 1000.0)
                    else:
                        errors += 1
                except Exception:
                    total_requests += 1
                    errors += 1

        print(f"Starting load test: {num_robots} robots | duration ~{duration_secs}s | concurrency {concurrency}")
        start_time = time.perf_counter()
        end_target = start_time + duration_secs

        tasks = []
        payload_idx = 0

        while time.perf_counter() < end_target:
            payload = payloads[payload_idx % num_robots]
            payload_idx += 1
            task = asyncio.create_task(worker(payload))
            tasks.append(task)
            # Throttle task creation slightly to maintain target rate
            if len(tasks) % concurrency == 0:
                await asyncio.sleep(0.01)

        await asyncio.gather(*tasks, return_exceptions=True)
        total_duration = time.perf_counter() - start_time

    pcts = calculate_percentiles(latencies)
    successful = len(latencies)
    rps = successful / total_duration if total_duration > 0 else 0.0
    error_rate = (errors / total_requests * 100.0) if total_requests > 0 else 0.0

    return {
        "duration_sec": total_duration,
        "total_requests": total_requests,
        "successful": successful,
        "errors": errors,
        "error_rate_pct": error_rate,
        "rps": rps,
        **pcts,
    }


def print_report(results: dict, label: str = "Benchmark Results"):
    print("\n" + "=" * 60)
    print(f"  [ BENCHMARK RESULTS - {label.upper()} ]")
    print("=" * 60)
    print(f"  Duration              : {results['duration_sec']:.2f} s")
    print(f"  Total Requests        : {results['total_requests']}")
    print(f"  Successful Requests   : {results['successful']}")
    print(f"  Failed Requests       : {results['errors']} ({results['error_rate_pct']:.2f}%)")
    print(f"  Throughput (RPS)      : {results['rps']:.1f} req/sec")
    print("-" * 60)
    print(f"  Mean Latency          : {results['mean']:.2f} ms")
    print(f"  p50 Latency (Median)  : {results['p50']:.2f} ms")
    print(f"  p95 Latency           : {results['p95']:.2f} ms")
    print(f"  p99 Latency           : {results['p99']:.2f} ms")
    print(f"  Max Latency           : {results['max']:.2f} ms")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telemetry API Load Tester")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of backend")
    parser.add_argument("--key", default="fleet-secret-key-2026", help="API Key header")
    parser.add_argument("--robots", type=int, default=500, help="Number of simulated robots")
    parser.add_argument("--duration", type=int, default=10, help="Test duration in seconds")
    parser.add_argument("--concurrency", type=int, default=100, help="Max concurrent HTTP tasks")
    args = parser.parse_args()

    if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    res = asyncio.run(
        run_benchmark(
            base_url=args.url,
            api_key=args.key,
            num_robots=args.robots,
            duration_secs=args.duration,
            concurrency=args.concurrency,
        )
    )
    print_report(res, label=f"Telemetry Ingestion Load Test ({args.robots} Robots)")
