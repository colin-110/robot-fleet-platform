import asyncio
import httpx
import time
import argparse
from typing import List

async def send_telemetry_burst(client: httpx.AsyncClient, num_requests: int) -> tuple[int, float]:
    url = "http://localhost:8000/api/v1/telemetry"
    
    # Pre-generate payloads to avoid measuring CPU time for dict creation
    payloads = []
    for i in range(num_requests):
        payloads.append({
            "robot_id": i + 1,
            "battery": 85.5,
            "temperature": 45.2,
            "speed": 1.2,
            "status": "ACTIVE",
            "x": 0.0,
            "y": 0.0,
            "battery_health": 95,
            "motor_health": 92,
            "sensor_health": 98,
            "network_health": 100
        })

    start_time = time.time()
    headers = {"X-API-Key": "fleet-secret-key-2026"}
    
    async def post_request(payload):
        req_start = time.time()
        try:
            resp = await client.post(url, json=payload, headers=headers)
            req_end = time.time()
            return resp.status_code == 200, (req_end - req_start) * 1000
        except Exception:
            return False, 0.0

    tasks = [post_request(p) for p in payloads]
    results = await asyncio.gather(*tasks)
    
    end_time = time.time()
    
    successes = [r for r in results if r[0]]
    success_count = len(successes)
    avg_latency = sum(r[1] for r in successes) / success_count if success_count > 0 else 0
    total_time = end_time - start_time
    
    return success_count, total_time, avg_latency

async def run_test(robots: int):
    print(f"\n--- Testing with {robots} robots ---")
    
    # Warmup
    print("Warming up (50 req)...")
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=10000, max_keepalive_connections=10000)) as client:
        await send_telemetry_burst(client, 50)
        await asyncio.sleep(1)
        
        # Test iterations
        iterations = 3
        total_rps = 0
        total_latency = 0
        total_success = 0
        
        for i in range(iterations):
            print(f"Iteration {i+1}...")
            success, duration, avg_lat = await send_telemetry_burst(client, robots)
            
            rps = robots / duration
            
            print(f"  Success: {success}/{robots} in {duration:.2f}s (RPS: {rps:.0f}, Avg Latency: {avg_lat:.2f}ms)")
            total_rps += rps
            total_success += success
            total_latency += avg_lat
            
        print(f"-> Average RPS: {total_rps/iterations:.0f}")
        print(f"-> Average Latency: {total_latency/iterations:.2f}ms")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--robots", type=int, default=500)
    args = parser.parse_args()
    
    # Use default ProactorEventLoop on Windows (supports >512 sockets)
    if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    asyncio.run(run_test(args.robots))
