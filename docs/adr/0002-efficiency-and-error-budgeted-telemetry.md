# ADR 0002: Efficiency design and error-budgeted telemetry

Status: accepted; build step 1 done. Builds on [ADR 0001](0001-mqtt-and-timescale.md).

## Goal

Run a real fleet on the smallest server possible, with zero data loss, and make
every number on the dashboard honest about how accurate it is. Everything here
is measured by one headline metric: **cost per robot per month**.

Targets (to be measured, not assumed):

| Metric | Target |
| :--- | :--- |
| Server | 512 MB RAM, ARM (t4g.nano or a 512 MB Lightsail plan) |
| Fleet on that server | 24 robots with headroom |
| Average uplink | under one message per robot every 10 s while healthy |
| Bytes per robot per day | under 1 MB |
| Data loss under `kill -9` of the app or broker | zero |
| Cost per robot per month | about $0.20 |

## The idea: error-budgeted telemetry

Most monitoring sends readings on a fixed timer and shows whatever arrived
last, however stale. This design inverts that.

### 1. Dual prediction

The robot agent and the server run the same predictor for each channel:

- position: `p(t) = p0 + v0 * (t - t0)` from the last sent position and velocity
- battery: `b(t) = b0 - d0 * (t - t0)` from the last sent level and drain rate
- slow channels (temperature, component health): hold the last value

On every sensor tick the agent compares reality with the shared prediction.
It sends a **keyframe** (current value plus current rate) only when

- the error on any channel exceeds that channel's tolerance `eps`, or
- a discrete field changes (status, mission, alarm), or
- the heartbeat interval passes with nothing sent.

Both sides then reset the predictor from the keyframe. Between keyframes the
server and dashboard compute the value themselves, and it is guaranteed to be
within `eps` of what the robot measured. This generalises swinging-door
compression (which assumes straight lines in time) to physics the robot
actually follows, which matters because a moving robot is not a straight line
in x or y alone.

Consequences that follow from it:

- **Storage holds keyframes only.** Any instant can be reconstructed exactly as
  the dashboard showed it.
- **Rollups are exact.** Between keyframes the signal is a known line, so
  time-weighted averages are integrals over segments, not `AVG()` over rows
  (which is wrong once rows are unevenly spaced).
- **The dashboard animates for free.** It runs the same predictor, so the map
  moves smoothly even at one message a minute.
- **Every value carries a bound.** The UI shows "position ±0.3 m", "battery ±0.5%".

### 2. Budget allocation

The operator sets a fleet budget `B` (messages per minute). The server chooses
each robot's tolerance to spend that budget where it matters.

For robot `i`, the observed message rate at tolerance `eps` is modelled as
`rate_i = c_i / eps^2`: for a signal that wanders like a random walk, the time
to drift past `eps` grows with `eps^2`. `c_i` is estimated online from the
robot's own history (observed rate times `eps^2`).

Each robot also has a priority weight `w_i`: higher while it has an active
alarm, an anomaly score above threshold, or a viewer looking at it.

Minimise total weighted error subject to the budget:

```
minimise   sum_i  w_i * eps_i
subject to sum_i  c_i / eps_i^2  <=  B
```

The Lagrangian gives a closed form, `eps_i = (2 * lambda * c_i / w_i)^(1/3)`,
and `lambda` is found by bisection so the budget is met. Tolerances are clamped
to per-channel minimums and maximums and recomputed every 30 s.

The result goes to each robot as **desired state** on a retained topic
(`fleet/<id>/config`), and the robot reports what it applied
(`fleet/<id>/config/reported`): the standard device-shadow pattern.

## Optimizations by layer

### Robot agent (a pip package; ROS 2 robots use `mqtt_client`)

1. **Dual-prediction keyframes** as above.
2. **Compact encoding.** CBOR payloads with short field names and only changed
   fields; MQTT 5 topic aliases so the topic string is not resent.
3. **On-disk outbox.** A bounded SQLite ring buffer, so readings survive network
   loss and agent restarts. QoS 1 handles redelivery to the broker.
4. **Applies desired config** (tolerances, heartbeat) and reports it back.

### Broker (Mosquitto + go-auth)

5. **The broker is the queue.** QoS 1 and persistent sessions; the app acks a
   message only after its batch is committed. No Redis, Kafka or worker.
6. **Retained state.** Presence and desired config are retained, so a dashboard
   or robot that connects gets current state without a database query.
7. **Last will.** The broker publishes `offline` the moment a connection drops.

### App (one async process, separate modules)

8. **Micro-batched writes.** One `INSERT ... ON CONFLICT DO NOTHING` per batch,
   flushed at 200 messages or 250 ms. Batching is what keeps the cost of full
   durability (`synchronous_commit=on`) low.
9. **In-memory fleet state.** The current state and predictor of every robot
   live in a dictionary; `robot_state` is written at most every few seconds per
   robot (write coalescing). Dashboard reads come from memory.
10. **Streaming intelligence.** Constant memory per robot, no history queries:
    - battery runtime from a two-state Kalman filter (level, drain), with an
      error range
    - anomaly score relative to fleet peers (robust z-score: median and MAD)
    - offline suspicion (phi accrual over the keyframe/heartbeat arrival times)
11. **Alarms with hysteresis.** Separate raise and clear thresholds, a minimum
    duration, deduplication, an acknowledge/clear lifecycle, and webhook
    notifications. No alert storms.
12. **Viewport-aware WebSocket.** Each browser subscribes to the robots it is
    showing; updates are coalesced to a few per second.

### Storage (TimescaleDB)

13. **Compression** (done in phase 1), segmented by robot.
14. **Rollups and tiered retention.** Exact time-weighted 1-minute and 1-hour
    rollups built from keyframe segments; raw keyframes kept 30 days, 1-minute
    rollups 1 year, 1-hour rollups indefinitely. Charts read rollups.

### Dashboard (static build on a CDN)

15. **Runs the shared predictor** between updates and shows each value's bound.

### Operations

16. **512 MB memory budget**, measured: Postgres tuned small, one app process,
    Mosquitto, the simulator, and swap.
17. **Benchmark harness** that reports messages, bytes, CPU, RAM and database
    growth per robot, and the resulting cost per robot per month.

## Build order

Each step ships with tests and a measurement, and is committed separately.

1. **Finish phase 1 of ADR 0001.** Remove the ingest flags, Redis stream path,
   deferred buffer, HTTP acks and viewer gating.
2. **MQTT core.** Mosquitto with go-auth on device keys; ingest consumer with
   ack after commit (paho, manual acks); presence from last will; commands
   pushed over MQTT; WebSocket fan-out from the app; Redis removed.
3. **Robot agent package.** Message schema, CBOR, on-disk outbox, desired and
   reported config. The simulator is rebuilt on top of it.
4. **Dual prediction.** Shared predictor (Python, with a matching JavaScript
   version for the dashboard), keyframe storage, reconstruction.
5. **Streaming intelligence and alarms.** Kalman battery, peer anomalies, phi
   accrual, alarm lifecycle, webhooks.
6. **Budget allocator.** Online `c_i` estimates, weights, the closed-form
   allocation, desired config.
7. **Storage tiers.** Exact rollups from segments and the retention tiers.
8. **Dashboard.** Predictor-driven animation, error bounds, viewport
   subscriptions, alarm views.
9. **Benchmark and the 512 MB test.** Publish the numbers in the README.
10. **Deployment.** The smallest instance, TLS on MQTT, backups to S3, budget
    alerts.

## Risks and how they are handled

- **Clock skew between robot and server.** Predictions are evaluated in device
  time carried in each keyframe, so skew does not create error.
- **A wrong motion model.** It only costs bandwidth: the error bound still
  holds, because the robot sends as soon as reality diverges.
- **Lost keyframes.** QoS 1 plus the on-disk outbox; a per-robot sequence
  number exposes any gap.
- **Unbounded rates from a misbehaving robot.** The broker enforces per-client
  rate limits; the allocator's tolerances are a request, not a guarantee.
- **512 MB is tight.** Step 9 measures it before deployment commits to it;
  t4g.micro is the fallback.
