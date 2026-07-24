"""
Robot Fleet Platform — Performance & Stress Test Suite

Tests:
  1. Single telemetry POST throughput
  2. Batch telemetry POST throughput
  3. REST GET latency (robots/status, analytics/fleet)
  4. WebSocket message throughput
  5. Sustained mixed load (60 seconds)
  6. Database write verification

Usage:
  pip install aiohttp websockets
  python stress_test.py --base-url http://localhost:8000
"""

import argparse
import asyncio
import json
import random
import statistics
import time
import sys
from dataclasses import dataclass, field

import aiohttp

API_KEY = "new-secure-api-key-889900"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


@dataclass
class TestResult:
    name: str
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    latencies_ms: list = field(default_factory=list)
    duration_s: float = 0.0
    errors: list = field(default_factory=list)

    @property
    def rps(self):
        return self.successful / self.duration_s if self.duration_s > 0 else 0

    @property
    def p50(self):
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0

    @property
    def p95(self):
        if not self.latencies_ms:
            return 0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def p99(self):
        if not self.latencies_ms:
            return 0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.99)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def avg(self):
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0

    def summary(self):
        return (
            f"  {self.name}\n"
            f"    Total: {self.total_requests}  |  OK: {self.successful}  |  Fail: {self.failed}\n"
            f"    Duration: {self.duration_s:.2f}s  |  RPS: {self.rps:.1f}\n"
            f"    Latency -- avg: {self.avg:.1f}ms  |  p50: {self.p50:.1f}ms  |  p95: {self.p95:.1f}ms  |  p99: {self.p99:.1f}ms\n"
        )


def make_telemetry_payload(robot_id=None):
    rid = robot_id or random.randint(1, 200)
    return {
        "robot_id": rid,
        "battery": round(random.uniform(20, 100), 2),
        "temperature": round(random.uniform(25, 70), 2),
        "speed": round(random.uniform(0, 2.5), 2),
        "status": random.choice(["ACTIVE", "CHARGING", "LOW POWER", "OVERHEATING"]),
        "mission_id": f"M-{random.randint(1000, 9999)}",
        "mission_type": random.choice(["PATROL", "DELIVERY", "INSPECTION"]),
        "mission_progress": round(random.uniform(0, 100), 1),
        "battery_health": round(random.uniform(70, 100), 1),
        "motor_health": round(random.uniform(70, 100), 1),
        "sensor_health": round(random.uniform(70, 100), 1),
        "network_health": round(random.uniform(70, 100), 1),
        "x": round(random.uniform(-50, 50), 2),
        "y": round(random.uniform(-50, 50), 2),
    }


# -- Test 1: Single Telemetry POST --

async def test_single_telemetry(session, base_url, concurrency, num_requests):
    url = f"{base_url}/api/v1/telemetry"
    result = TestResult(name=f"Single POST /telemetry (concurrency={concurrency})")
    sem = asyncio.Semaphore(concurrency)

    async def do_request():
        async with sem:
            payload = make_telemetry_payload()
            start = time.monotonic()
            try:
                async with session.post(url, json=payload, headers=HEADERS) as resp:
                    elapsed = (time.monotonic() - start) * 1000
                    result.total_requests += 1
                    if resp.status == 200:
                        result.successful += 1
                        result.latencies_ms.append(elapsed)
                    else:
                        result.failed += 1
                        body = await resp.text()
                        result.errors.append(f"HTTP {resp.status}: {body[:100]}")
            except Exception as e:
                result.total_requests += 1
                result.failed += 1
                result.errors.append(str(e)[:100])

    t0 = time.monotonic()
    tasks = [do_request() for _ in range(num_requests)]
    await asyncio.gather(*tasks)
    result.duration_s = time.monotonic() - t0
    return result


# -- Test 2: Batch Telemetry POST --

async def test_batch_telemetry(session, base_url, concurrency, num_batches, batch_size=50):
    url = f"{base_url}/api/v1/telemetry/batch"
    result = TestResult(name=f"Batch POST /telemetry/batch (concurrency={concurrency}, batch={batch_size})")
    sem = asyncio.Semaphore(concurrency)

    async def do_request():
        async with sem:
            payload = [make_telemetry_payload() for _ in range(batch_size)]
            start = time.monotonic()
            try:
                async with session.post(url, json=payload, headers=HEADERS) as resp:
                    elapsed = (time.monotonic() - start) * 1000
                    result.total_requests += 1
                    if resp.status == 200:
                        result.successful += 1
                        result.latencies_ms.append(elapsed)
                    else:
                        result.failed += 1
                        body = await resp.text()
                        result.errors.append(f"HTTP {resp.status}: {body[:100]}")
            except Exception as e:
                result.total_requests += 1
                result.failed += 1
                result.errors.append(str(e)[:100])

    t0 = time.monotonic()
    tasks = [do_request() for _ in range(num_batches)]
    await asyncio.gather(*tasks)
    result.duration_s = time.monotonic() - t0
    return result


# -- Test 3: REST GET Latency --

async def test_get_endpoint(session, base_url, path, concurrency, num_requests, name=None):
    url = f"{base_url}{path}"
    result = TestResult(name=name or f"GET {path} (concurrency={concurrency})")
    sem = asyncio.Semaphore(concurrency)

    async def do_request():
        async with sem:
            start = time.monotonic()
            try:
                async with session.get(url) as resp:
                    elapsed = (time.monotonic() - start) * 1000
                    result.total_requests += 1
                    if resp.status == 200:
                        result.successful += 1
                        result.latencies_ms.append(elapsed)
                    else:
                        result.failed += 1
                        body = await resp.text()
                        result.errors.append(f"HTTP {resp.status}: {body[:100]}")
            except Exception as e:
                result.total_requests += 1
                result.failed += 1
                result.errors.append(str(e)[:100])

    t0 = time.monotonic()
    tasks = [do_request() for _ in range(num_requests)]
    await asyncio.gather(*tasks)
    result.duration_s = time.monotonic() - t0
    return result


# -- Test 4: WebSocket Throughput --

async def test_websocket(base_url, num_clients, duration_s=10):
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/ws?api_key={API_KEY}"
    result = TestResult(name=f"WebSocket ({num_clients} clients, {duration_s}s)")

    message_counts = []

    async def ws_client(client_id):
        count = 0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=duration_s + 5)) as ws:
                    end_time = time.monotonic() + duration_s
                    while time.monotonic() < end_time:
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                if msg.data != "ping":
                                    count += 1
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                        except asyncio.TimeoutError:
                            continue
        except Exception as e:
            result.errors.append(f"Client {client_id}: {str(e)[:80]}")
            result.failed += 1

        message_counts.append(count)
        result.successful += 1

    t0 = time.monotonic()
    tasks = [ws_client(i) for i in range(num_clients)]
    await asyncio.gather(*tasks)
    result.duration_s = time.monotonic() - t0
    result.total_requests = num_clients

    total_msgs = sum(message_counts)
    avg_msgs = total_msgs / len(message_counts) if message_counts else 0
    print(f"    WebSocket: {total_msgs} total messages across {num_clients} clients ({avg_msgs:.0f} avg/client)")
    print(f"    Throughput: {total_msgs / duration_s:.0f} msgs/sec total")
    return result


# -- Test 5: Sustained Mixed Load --

async def test_sustained_load(session, base_url, duration_s=30, concurrency=50):
    result = TestResult(name=f"Sustained mixed load ({duration_s}s, concurrency={concurrency})")
    sem = asyncio.Semaphore(concurrency)
    stop_event = asyncio.Event()

    async def worker():
        while not stop_event.is_set():
            async with sem:
                roll = random.random()
                start = time.monotonic()
                try:
                    if roll < 0.6:
                        payload = make_telemetry_payload()
                        async with session.post(f"{base_url}/api/v1/telemetry", json=payload, headers=HEADERS) as resp:
                            elapsed = (time.monotonic() - start) * 1000
                            result.total_requests += 1
                            if resp.status == 200:
                                result.successful += 1
                                result.latencies_ms.append(elapsed)
                            else:
                                result.failed += 1
                    elif roll < 0.8:
                        async with session.get(f"{base_url}/api/v1/robots/status") as resp:
                            elapsed = (time.monotonic() - start) * 1000
                            result.total_requests += 1
                            if resp.status == 200:
                                result.successful += 1
                                result.latencies_ms.append(elapsed)
                            else:
                                result.failed += 1
                    else:
                        async with session.get(f"{base_url}/api/v1/analytics/fleet") as resp:
                            elapsed = (time.monotonic() - start) * 1000
                            result.total_requests += 1
                            if resp.status == 200:
                                result.successful += 1
                                result.latencies_ms.append(elapsed)
                            else:
                                result.failed += 1
                except Exception as e:
                    result.total_requests += 1
                    result.failed += 1
                    result.errors.append(str(e)[:80])

            await asyncio.sleep(0.001)

    t0 = time.monotonic()
    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await asyncio.sleep(duration_s)
    stop_event.set()
    await asyncio.gather(*workers, return_exceptions=True)
    result.duration_s = time.monotonic() - t0
    return result


# -- Test 6: Database Write Verification --

async def test_db_write_verification(session, base_url):
    result = TestResult(name="DB Write Verification (end-to-end)")

    unique_id = random.randint(9000, 9999)
    payload = make_telemetry_payload(robot_id=unique_id)

    t0 = time.monotonic()
    try:
        async with session.post(f"{base_url}/api/v1/telemetry", json=payload, headers=HEADERS) as resp:
            result.total_requests += 1
            if resp.status == 200:
                result.successful += 1
            else:
                result.failed += 1
                return result

        await asyncio.sleep(3.0)

        async with session.get(f"{base_url}/api/v1/robots/status") as resp:
            result.total_requests += 1
            if resp.status == 200:
                data = await resp.json()
                found = any(r.get("robot_id") == unique_id for r in data)
                if found:
                    result.successful += 1
                    print(f"    OK: Robot {unique_id} found in fleet status after telemetry ingestion")
                else:
                    result.failed += 1
                    print(f"    WARN: Robot {unique_id} NOT found -- may need more time for worker flush")
            else:
                result.failed += 1
    except Exception as e:
        result.total_requests += 1
        result.failed += 1
        result.errors.append(f"DB verification error: {str(e)[:80]}")


    result.duration_s = time.monotonic() - t0
    return result


# -- Main Runner --

async def run_all_tests(base_url):
    print("=" * 70)
    print("ROBOT FLEET PLATFORM -- PERFORMANCE & STRESS TEST")
    print("=" * 70)
    print(f"Target: {base_url}")
    print()

    connector = aiohttp.TCPConnector(limit=2500)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(f"{base_url}/health") as resp:
                health = await resp.json()
                print(f"Health: {health}")
                if health.get("database") != "healthy":
                    print("ERROR: Database is not healthy! Aborting.")
                    return
        except Exception as e:
            print(f"ERROR: Cannot reach backend: {e}")
            return

        print()
        all_results = []

        # Test 1
        print("-" * 70)
        print("TEST 1: Single Telemetry POST Throughput")
        print("-" * 70)
        for conc in [1, 10, 50, 100, 500, 1500, 2000]:
            r = await test_single_telemetry(session, base_url, concurrency=conc, num_requests=conc * 2)
            print(r.summary())
            all_results.append(r)

        # Test 2
        print("-" * 70)
        print("TEST 2: Batch Telemetry POST Throughput")
        print("-" * 70)
        for conc in [1, 10, 50, 100, 500]:
            r = await test_batch_telemetry(session, base_url, concurrency=conc, num_batches=conc * 2, batch_size=50)
            print(r.summary())
            all_results.append(r)

        # Test 3
        print("-" * 70)
        print("TEST 3: REST GET Latency")
        print("-" * 70)
        for conc in [1, 10, 50, 500, 1500, 2000]:
            r = await test_get_endpoint(session, base_url, "/api/v1/robots/status", conc, conc * 2,
                                         name=f"GET /robots/status (concurrency={conc})")
            print(r.summary())
            all_results.append(r)

        for conc in [1, 10, 50, 500, 1500, 2000]:
            r = await test_get_endpoint(session, base_url, "/api/v1/analytics/fleet", conc, conc * 2,
                                         name=f"GET /analytics/fleet (concurrency={conc})")
            print(r.summary())
            all_results.append(r)

    # Test 4
    print("-" * 70)
    print("TEST 4: WebSocket Throughput")
    print("-" * 70)
    for clients in [5, 20, 50, 500, 1500, 2000]:
        r = await test_websocket(base_url, num_clients=clients, duration_s=10)
        print(r.summary())
        all_results.append(r)

    # Test 5 + 6
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=2500)) as session:
        print("-" * 70)
        print("TEST 5: Sustained Mixed Load (30 seconds)")
        print("-" * 70)
        r = await test_sustained_load(session, base_url, duration_s=30, concurrency=50)
        print(r.summary())
        all_results.append(r)

        print("-" * 70)
        print("TEST 6: Database Write Verification")
        print("-" * 70)
        r = await test_db_write_verification(session, base_url)
        print(r.summary())
        all_results.append(r)

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_requests = sum(r.total_requests for r in all_results)
    total_ok = sum(r.successful for r in all_results)
    total_fail = sum(r.failed for r in all_results)

    if total_requests:
        print(f"  Total Requests: {total_requests}")
        print(f"  Successful:     {total_ok} ({total_ok/total_requests*100:.1f}%)")
        print(f"  Failed:         {total_fail} ({total_fail/total_requests*100:.1f}%)")
    print()

    all_errors = []
    for r in all_results:
        all_errors.extend(r.errors[:5])
    if all_errors:
        print("  Sample Errors:")
        for e in list(set(all_errors))[:10]:
            print(f"    - {e}")
    else:
        print("  No errors encountered!")

    print()
    print("  Best throughput tests:")
    throughput_tests = [r for r in all_results if r.rps > 0 and "WebSocket" not in r.name and "Verification" not in r.name]
    throughput_tests.sort(key=lambda r: r.rps, reverse=True)
    for r in throughput_tests[:5]:
        print(f"    {r.name}: {r.rps:.0f} req/s (p99={r.p99:.0f}ms)")

    print()
    return all_results


def main():
    parser = argparse.ArgumentParser(description="Robot Fleet Platform Stress Test")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend URL")
    args = parser.parse_args()

    asyncio.run(run_all_tests(args.base_url))


if __name__ == "__main__":
    main()
