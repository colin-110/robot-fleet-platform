# Benchmark

[← Back to README](../README.md)

`python3 bench/run.py` starts the whole product (TimescaleDB, the broker, the
API and the simulator) from [`bench/docker-compose.yml`](../bench/docker-compose.yml),
each container capped so the four add up to 480 MB, runs it for ten minutes,
and writes [`bench/results.md`](../bench/results.md). The numbers below are
from that file: 24 simulated robots, 2026-11-08, on a laptop (x86, Docker).

## Results against the targets in ADR 0002

| Target | Goal | Measured | |
| :--- | :--- | :--- | :--- |
| Fits a 512 MB server | yes | 261 MB peak for all four containers, no out-of-memory kills | met, with the OS and swap to add |
| Average uplink | under 6 messages per robot per minute | 18.2 | missed |
| Bytes per robot per day | under 1 MB | 3.9 MB | missed |
| Data loss under `kill -9` | zero | zero ([architecture](architecture.md#measured-durability)) | met |
| Cost per robot per month | about $0.20 | $0.33 on a t4g.nano | missed by a third |

| Container | Limit | Peak | Mean | CPU (one core) |
| :--- | ---: | ---: | ---: | ---: |
| TimescaleDB | 200 MB | 129 MB | 113 MB | 3.5 % |
| Mosquitto + go-auth | 48 MB | 14 MB | 13 MB | 1.1 % |
| API | 160 MB | 78 MB | 77 MB | 6.6 % |
| Simulator (24 robots) | 72 MB | 40 MB | 39 MB | 2.4 % |

Robots took 33 sensor readings a minute each and sent 18 keyframes, in 18
MQTT messages (readings are seconds apart, so batching rarely combines
them). The database grew 6.6 MB per robot per day before compression and
2.8 MB after it; that ratio is measured on ten minutes of data, and a full
day's chunk, which is what the compression policy acts on, usually
compresses further.

## Reading the misses

The uplink and byte targets were set for a fleet that is mostly cruising.
The simulator is a stress case instead: its batteries go from full to empty
in about four minutes, so robots constantly switch between missions,
charging and repairs, and they stop and start at waypoints every few
seconds. Every stop and every start is a large prediction error at any
tolerance. Two measurements bound what dual prediction can do for this fleet:

- At the default tolerances, 55 % fewer messages than sending every reading
  (step 4).
- At the loosest tolerances allowed (eight times the defaults), 5.3
  keyframes per robot per minute, against 14 at the defaults (step 6). That
  is the floor the budget allocator reports as unreachable below.

So for this simulator the 6-a-minute target is reachable only by setting a
budget at the loosest tolerances, and the cost follows the message rate
only weakly: memory, not messages, sets the instance size, and a t4g.nano
holds this fleet with room left. At 40 robots on the same instance the cost
would be $0.20 a robot, if it still fits; the benchmark has not measured
that yet (`python3 bench/run.py --robots 40`).

## What is not measured here

- **The operating system.** A t4g.nano has 512 MB for everything; Ubuntu and
  the Docker daemon take roughly 150 MB more than the containers, which the
  bootstrap's 1 GB of swap covers. Run the benchmark on the instance itself
  before relying on it; a t4g.micro is the fallback.
- **ARM.** These numbers are from x86. Memory should be close on Graviton;
  CPU will differ.
- **Long runs.** Ten minutes shows no growth in any container, but not the
  first compression and retention cycles, which happen after a day.
