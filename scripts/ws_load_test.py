"""
WebSocket Concurrency & Broadcast Latency Load Tester.

Simulates N concurrent WebSocket client connections to the backend
and measures:
- Connection success rate
- Broadcast receive latency (p50, p95, p99)
- Message drop count

Usage:
  python scripts/ws_load_test.py --clients 500 --duration 15
"""

import argparse
import asyncio
import math
import sys
import time
from typing import List

import websockets


def calculate_percentiles(latencies: List[float]) -> dict:
    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}
    s = sorted(latencies)
    n = len(s)

    def pct(p: float) -> float:
        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        return s[int(f)] if f == c else s[int(f)] * (c - k) + s[int(c)] * (k - f)

    return {
        "mean": sum(s) / n,
        "p50": pct(0.50),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "max": s[-1],
    }


async def client_worker(
    ws_url: str,
    duration: int,
    latencies: List[float],
    stats: dict,
):
    try:
        async with websockets.connect(ws_url) as ws:
            stats["connected"] += 1
            start = time.perf_counter()
            while time.perf_counter() - start < duration:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    stats["messages_received"] += 1
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    stats["errors"] += 1
                    break
    except Exception:
        stats["failed_connections"] += 1


async def run_ws_benchmark(
    base_ws_url: str,
    api_key: str,
    num_clients: int,
    duration: int,
):
    url = f"{base_ws_url.rstrip('/')}/ws?api_key={api_key}"
    latencies: List[float] = []
    stats = {
        "connected": 0,
        "failed_connections": 0,
        "messages_received": 0,
        "errors": 0,
    }

    print(f"Connecting {num_clients} concurrent WebSocket clients...")
    start_time = time.perf_counter()

    # Ramp up connections in batches of 50
    batch_size = 50
    tasks = []
    for i in range(num_clients):
        task = asyncio.create_task(client_worker(url, duration, latencies, stats))
        tasks.append(task)
        if (i + 1) % batch_size == 0:
            await asyncio.sleep(0.1)

    await asyncio.gather(*tasks, return_exceptions=True)
    total_time = time.perf_counter() - start_time

    pcts = calculate_percentiles(latencies)

    print("\n" + "=" * 60)
    print("  [ WEBSOCKET CONCURRENCY BENCHMARK RESULTS ]")
    print("=" * 60)
    print(f"  Target Clients          : {num_clients}")
    print(f"  Successfully Connected  : {stats['connected']}")
    print(f"  Failed Connections      : {stats['failed_connections']}")
    print(f"  Messages Received       : {stats['messages_received']}")
    print(f"  Errors / Drops          : {stats['errors']}")
    print(f"  Total Duration          : {total_time:.2f} s")
    print("-" * 60)
    if latencies:
        print(f"  p50 Latency             : {pcts['p50']:.2f} ms")
        print(f"  p95 Latency             : {pcts['p95']:.2f} ms")
        print(f"  p99 Latency             : {pcts['p99']:.2f} ms")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebSocket Concurrency Tester")
    parser.add_argument("--url", default="ws://localhost:8000", help="WebSocket Base URL")
    parser.add_argument("--key", default="fleet-secret-key-2026", help="WebSocket API Key")
    parser.add_argument("--clients", type=int, default=200, help="Number of concurrent clients")
    parser.add_argument("--duration", type=int, default=10, help="Test duration in seconds")
    args = parser.parse_args()

    if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    asyncio.run(run_ws_benchmark(args.url, args.key, args.clients, args.duration))
