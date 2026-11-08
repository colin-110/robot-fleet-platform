# ADR 0002: Efficiency design and error-budgeted telemetry

Status: accepted; build steps 1 to 8 done. Builds on [ADR 0001](0001-mqtt-and-timescale.md).

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
2. **Compact encoding.** CBOR payloads with short field names, fixed-point
   numbers, times relative to the send time and only changed fields (done:
   about 130 bytes for a reading alone, 20 to 35 inside a batch, against about
   450 as JSON). MQTT 5 topic aliases were left out: paho resends unconfirmed
   messages after a reconnect, when their aliases no longer mean anything, and
   the topic is only 18 bytes.
3. **On-disk outbox.** A bounded SQLite queue (done), kept until the server
   confirms the items are committed, not merely until the broker's PUBACK.
4. **Applies desired config** and reports it back (done for the reporting
   interval; tolerances and the heartbeat arrive with step 4).

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
3. **Robot agent package** (done). Message schema, CBOR, on-disk outbox, an
   end-to-end stored watermark, desired and reported config, correction of
   fast clocks. The simulator is rebuilt on top of it.
4. **Dual prediction** (done). Shared predictor in Python
   (`agent/fleetops_agent/predict.py`, copied to the API) and JavaScript (the
   dashboard), checked against one set of test vectors; keyframes stored with
   their rates; fleet status carried forward to now with its bounds;
   `GET /robots/<id>/track` rebuilds any time range. Measured with the
   simulator (20 robots, 90 s, default tolerances): 443 messages instead of
   994 (55% fewer) and 64 kB instead of 125 kB. Most remaining keyframes come
   from the simulator's per-tick speed changes, pauses at waypoints and sensor
   noise spikes; the tolerances are the lever, which step 6 sets. A unit test
   drives a robot along a patrol and checks that what the server shows is
   within tolerance at every tick.
5. **Streaming intelligence and alarms** (done). In `app/intelligence/`: a
   two-state Kalman filter per battery giving runtime with a two-sigma range,
   phi accrual over each robot's own message gaps, robust z-scores against
   fleet peers for temperature and drain. In `app/alarms/`: eight rules with
   separate raise and clear thresholds and hold times, one open alarm per
   robot and kind (a partial unique index), ACTIVE to ACKNOWLEDGED to CLEARED,
   WebSocket broadcasts and signed webhooks with retries, and a rule for a
   robot running twice (four new connections in a minute). Measured live (20
   simulated robots, 5 minutes): 15 battery alarms raised, 8 cleared on
   recharging, and the simulator's network blackouts caught as `SILENT`
   within seconds and cleared when the robot resumed. The first tuning of the
   battery filter assumed a near-constant drain and reported ranges far
   narrower than its error when a robot started a mission; the drain is now
   allowed to drift, faster when the robot reports its own rate.
6. **Budget allocator** (done). `app/budget/`: the closed-form allocation with
   `lambda` by bisection, each robot's demand learned online and its
   tolerance-independent floor fitted from two operating points, weights for
   alarms, anomalies and robots a dashboard is watching, and feedback on the
   fleet's measured total, which makes the total come out right however far
   the model is from a real robot. Measured live with the simulator (20
   robots, budget 70 % of the natural 289 a minute): from the third minute the
   fleet averaged 194 a minute against a budget of 202, swinging by about a
   third from minute to minute, as the simulated fleet does by itself (its
   batteries cycle every few minutes). Live runs found two bugs: the
   controller published the settings it had loaded instead of the ones it had
   just stored (a stale ORM object), and it handed the whole budget to the
   robots it happened to be measuring. A closed-loop test, against robots that
   do not follow the model, fails without the feedback.
7. **Storage tiers** (done). `app/storage/`: 1-minute and 1-hour rollups,
   time-weighted integrals over the predictor's segments (checked against a
   numerical integration of the predictor), recomputed for keyframes that
   arrive late; hypertables kept a year and indefinitely, raw keyframes 30
   days. `GET /robots/<id>/history` serves them, and the fleet health trend is
   time-weighted too.
8. **Dashboard** (done). The map animates robots with the shared predictor
   between keyframes and draws each one's error bound; cards show bounds and
   runtime ranges; an alarms panel with acknowledge; opening a robot on the
   map asks the server to watch it more closely. The WebSocket accepts
   per-robot subscriptions, but the dashboard keeps all robots, because its
   roster shows every one.
9. **Benchmark and the 512 MB test.** Publish the numbers in the README.
10. **Deployment.** The smallest instance, TLS on MQTT, backups to S3, budget
    alerts.

## Risks and how they are handled

- **Clock skew between robot and server.** Predictions are evaluated in device
  time carried in each keyframe, so skew does not create error.
- **A wrong motion model.** It only costs bandwidth: the error bound still
  holds, because the robot sends as soon as reality diverges.
- **Rounding.** Rates are sent as fixed-point integers, and rounding a
  velocity adds up over a minute. The robot therefore predicts from its
  keyframe as the server will decode it, not from its own unrounded values.
- **The bound holds at sensor ticks.** Between two ticks the robot itself does
  not know its error, so the guarantee is per tick, and the server stops
  extrapolating at the heartbeat, when a keyframe is overdue.
- **Lost keyframes.** QoS 1 plus the on-disk outbox; a per-robot sequence
  number exposes any gap. Measured in step 2: with `kill -9` of the API or the
  broker mid-stream, every reading was stored exactly once (five runs, 20
  robots at 10 readings per second). Step 3 closed the two gaps that remained,
  a robot crash and a broker that loses what it accepted, with a per-robot
  stored-up-to watermark: the robot keeps items on disk until the server says
  they are committed, and resends after 15 seconds without progress. Measured:
  a robot `kill -9` while it could not reach the broker, and a broker `kill -9`
  holding unsaved messages, each stored every reading exactly once in three
  runs, where the step 2 client lost 40 of 2,920 readings to the broker crash.
  The watermark only moves contiguously, and below the robot's floor (the
  lowest number it still holds), so an outbox that overflowed does not block
  it; the server counts those items as given up.
- **A robot clock far in the future.** Fixed in step 3 for the agent: its
  envelope carries the send time by the robot's clock, and anything later than
  the receive time is the clock's error, so the whole message moves back by it
  and readings keep their spacing. Plain JSON readings are still clamped one by
  one, which collapses a batch onto one timestamp.
- **A robot running twice.** Two processes with the same device key share one
  client id, so each connection takes over the other's in a loop. Each new
  connection id is counted, and four in a minute raise `DUPLICATE_SESSION`.
- **Unbounded rates from a misbehaving robot.** The broker enforces per-client
  rate limits; the allocator's tolerances are a request, not a guarantee.
- **One robot must not stop ingest for everyone.** A batch that fails to write
  is retried until it succeeds, which is right for a database outage and wrong
  for a batch that can never be written. Step 3 found two such batches (more
  parameters than Postgres allows in one statement, and values too long for
  their columns) and fixed both; the remaining defence is validating every
  value before it reaches the database.
- **512 MB is tight.** Step 9 measures it before deployment commits to it;
  t4g.micro is the fallback.
