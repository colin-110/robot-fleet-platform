import asyncio
import aiohttp
import time
import json
import statistics

async def fetch(session, url, headers, payload):
    start = time.perf_counter()
    async with session.post(url, headers=headers, json=payload) as response:
        await response.read()
    return time.perf_counter() - start

async def worker(session, url, headers, payload, duration, latencies, robot_id):
    end_time = time.time() + duration
    payload["robot_id"] = robot_id
    while time.time() < end_time:
        try:
            latency = await fetch(session, url, headers, payload)
            latencies.append(latency)
        except Exception as e:
            pass
        # Simulate 5-second interval between telemetries
        await asyncio.sleep(5)

async def main():
    url = "http://localhost:8000/api/v1/telemetry"
    headers = {"x-api-key": "fleet-secret-key-2026", "Content-Type": "application/json"}
    payload = {
        "battery": 85.5,
        "temperature": 45.0,
        "speed": 1.2,
        "status": "IDLE"
    }
    
    duration = 20  # Run for 20 seconds to see multiple 5s cycles
    num_robots = 2000 # 2000 robots reporting every 5s
    latencies = []
    
    print(f"Running {duration}s simulation with {num_robots} robots reporting every 5 seconds...")
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        start_time = time.time()
        for i in range(num_robots):
            tasks.append(asyncio.create_task(worker(session, url, headers, payload.copy(), duration, latencies, i)))
        
        await asyncio.gather(*tasks)
        actual_duration = time.time() - start_time

    if not latencies:
        print("No successful requests.")
        return

    latencies_ms = [l * 1000 for l in latencies]
    latencies_ms.sort()
    
    total_reqs = len(latencies_ms)
    rps = total_reqs / actual_duration
    p50 = latencies_ms[int(total_reqs * 0.50)]
    p95 = latencies_ms[int(total_reqs * 0.95)]
    p99 = latencies_ms[int(total_reqs * 0.99)]

    print(f"\n--- Simulation Results ---")
    print(f"Total Requests: {total_reqs}")
    print(f"Duration:       {actual_duration:.2f} s")
    print(f"Throughput:     {rps:.2f} req/s")
    print(f"P50 Latency:    {p50:.2f} ms")
    print(f"P95 Latency:    {p95:.2f} ms")
    print(f"P99 Latency:    {p99:.2f} ms")
    print(f"Min Latency:    {latencies_ms[0]:.2f} ms")
    print(f"Max Latency:    {latencies_ms[-1]:.2f} ms")

if __name__ == "__main__":
    asyncio.run(main())
