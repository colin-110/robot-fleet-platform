# ADR 0001: MQTT transport and TimescaleDB storage

Status: accepted, in progress (phase 1 done)

## Context

The first version used HTTP for everything robots do: telemetry uploads, command
polling, command status updates. Telemetry went into serverless Postgres (Neon)
on a free tier. Most of the later complexity came from those two choices:

- **Neon bills for awake time and has a small storage cap.** That led to a
  one-day retention window, prune loops, dropped indexes, an in-memory deferred
  write buffer to let the database sleep, and then a delivery-acknowledgement
  protocol to recover readings that buffer lost on a crash.
- **HTTP has no delivery guarantees or push.** Robots had to poll for commands
  (24 robots polling every few seconds exhausted a hosting bandwidth quota), and
  reliable delivery meant hand-building outboxes, acks and resends.
- **Three ingest modes** (Redis stream + worker, direct write, deferred buffer)
  selected by flags, each with different durability rules and tests.
- **Fleet status was recomputed from history** on every request, which is why a
  read cache existed.
- **Viewer gating** (collecting or saving less when nobody watches) was a cost
  hack in the core path. A monitoring system has to collect data either way.

## Decision

```
Robot agent ──MQTT QoS 1, device key auth, last will──► Mosquitto
                                                            │
                                              ingest service (one path)
                                 ┌──────────────────┬───────┴──────────┐
                           robot_state         telemetry           WebSocket
                         (1 row per robot)   (Timescale hypertable)   fan-out
Dashboard ◄── current state + rollups        compression, retention,
Commands ──MQTT QoS 1──► robot                continuous aggregates
```

1. **MQTT for robots.** Telemetry, events, command status and presence go over
   Mosquitto with QoS 1 and persistent sessions. The broker buffers for robots
   that are offline and for the ingest service while it restarts. Commands are
   pushed instead of polled. A last-will message marks a robot offline the moment
   its connection drops.
2. **Ack after commit.** The ingest service uses paho-mqtt with manual acks and
   acknowledges a message only after its batch is committed, so the broker keeps
   anything not yet stored. aiomqtt was ruled out because it acks on receipt.
3. **Device keys are the broker credentials.** mosquitto-go-auth calls the API
   to check a robot's key and restricts it to its own `fleet/<id>/...` topics.
4. **TimescaleDB for history.** `telemetry` becomes a hypertable with
   compression, a retention policy (replacing the prune loops) and a one-minute
   continuous aggregate for analytics. It runs next to the app, so there is no
   awake-time billing and no reason to buffer writes.
5. **A current-state table.** Every stored reading upserts `robot_state` if it is
   newer than what is there. The dashboard reads one row per robot; the read
   cache goes away.
6. **One ingest path.** The HTTP ingest endpoint stays as a thin adapter onto the
   same function (useful for tests and simple clients). The flags, the Redis
   stream, the worker's consumer, the deferred buffer and the ack protocol are
   removed. Redis is removed once fan-out moves to the broker.
7. **Idempotency stays.** At-least-once delivery means duplicates, so
   `(robot_id, timestamp)` remains unique and writes skip duplicates. Messages
   also carry `boot_id` and `seq` so gaps and reordering can be detected without
   trusting the device clock.

## Plan

**Phase 1, data layer** (HTTP transport unchanged)
- TimescaleDB in Compose and CI; hypertable, compression, retention, 1-minute aggregate
- `robot_state` table; fleet status reads from it; read cache removed
- Remove the ingest flags, Redis stream path, deferred buffer, acks and viewer gating

**Phase 2, MQTT**
- Mosquitto with go-auth against device keys and per-robot topic ACLs
- Ingest consumer: shared subscription, persistent session, ack after commit
- Commands and command status over MQTT; polling endpoints removed
- Presence from last-will messages
- WebSocket fan-out from the broker; Redis removed
- Simulator rebuilt on a small reusable robot agent

**Phase 3, smarter**
- Report-by-exception on the robot (swinging-door compression plus a heartbeat)
- Reporting rate set by the server through a retained config topic
- Offline detection: last will plus phi accrual
- Anomaly detection relative to fleet peers (robust z-scores)
- Battery runtime from a Kalman filter, with an error range
- Dead reckoning on the dashboard map between sparse reports

## Deployment and cost

Target: a few hundred dollars of AWS credits should last close to a year.
Everything runs on one Graviton instance with Docker Compose; the dashboard is a
static build on Vercel's free tier.

| Item | Approx. monthly (us-east-1) |
| :--- | ---: |
| EC2 t4g.small (2 vCPU, 2 GB, ARM) | $12.3 |
| Public IPv4 address | $3.6 |
| 20 GB gp3 volume | $1.6 |
| Nightly `pg_dump` to S3 | $0.1 |
| **Total** | **about $18** |

Deliberately not used, because each costs more than the whole stack: RDS, NAT
Gateway, a load balancer, ElastiCache, and AWS IoT Core (billed per message;
this fleet sends on the order of 18 million a month before report-by-exception).
Docker log rotation keeps CloudWatch out of the picture, and an AWS Budgets alert
guards against surprises. Images are built for arm64 in CI.

Report-by-exception (phase 3) is also a cost measure: it cuts messages, writes,
disk and bandwidth by an order of magnitude.

## Consequences

- One ingest path and no buffering workarounds; durability comes from the
  broker plus ack-after-commit.
- The database moves from Neon to a Timescale container on the app host. On a
  1 GB t3.micro that needs a small `shared_buffers` and swap; a 2 GB instance is
  more comfortable. Backups become our job (scheduled `pg_dump` to object storage).
- Compression and continuous aggregates are Timescale features that Neon does not
  offer, so the Neon deployment path is dropped.
- Robots need an MQTT client. ROS 2 robots can use an MQTT bridge.
