# ADR 0003: Storing each field as line segments with a guaranteed error

Status: accepted and built. Raw keyframes are still kept 30 days by default; lowering `RAW_RETENTION_DAYS` is what turns the saving below into freed disk. Builds on [ADR 0002](0002-efficiency-and-error-budgeted-telemetry.md).

## Problem

Dual prediction (ADR 0002) already stores only keyframes: a robot sends one when what it measures strays from the prediction the server shares. But a keyframe row holds every field, so whenever any one of them strays, usually position, all of them are written again. A battery that drains steadily for an hour, or a health value that does not move, is repeated in hundreds of rows.

## Decision

Store each numeric field on its own, as straight-line segments, `value(t) = v0 + slope * (t - t0)`, each within a tolerance of what is stored today.

- **Server side.** Robots and ingest are unchanged. A background job (`app/storage/lines_job.py`) turns each finished hour of keyframes into segments for every robot and field, replacing that hour's previous segments in one transaction, so a re-run changes nothing.
- **The guarantee holds at every instant, not only at stored points.** What is stored today is a chain of straight pieces (each keyframe carried forward by its rate, or held). A segment and that chain are both straight between the chain's corners, so their gap is largest at the corners, and the fit checks every corner: each keyframe's value and where its prediction stood when the next keyframe arrived (`app/storage/lines.py`). Total error against what the robot measured is at most the robot's tolerance plus this one.
- **Re-anchoring.** Each segment starts at a real value of the stored path, so error cannot carry from one segment into the next and never grows with time.
- **Unknown stays unknown.** Time with no keyframe in force (beyond the heartbeat, with the same 1.5x grace silence detection uses) has no segment.
- **Verified.** After fitting, the job measures every segment against the path it replaces, and counts and logs any that miss (`fleetops_lines_verify_failures_total`).
- **Late data.** A keyframe that arrives for an hour already done (an outbox replayed after an outage) marks that robot's hour, and the next one, to be redone.
- **Tolerance.** The robot's own tolerance for the field times `LINE_TOLERANCE_SCALE` (default 1).

The fit is the swing filter: keep the range of slopes that pass within tolerance of every point so far, close the segment when the range empties, and take the slope nearest zero, so a value that holds still is a flat line.

## Prior work

This is an established idea, not a new one:

- **Industrial historians** (AVEVA PI, formerly OSIsoft) have stored process data with "swinging door" compression for decades: keep only the points needed to redraw a signal as lines within a deviation.
- **Piecewise linear approximation** with an error bound dates to the 1960s; the swing filter is from Elmeleegy et al. (VLDB 2009); Sim-Piece (VLDB 2023) and Mix-Piece merge similar segments for more compression.
- **ModelarDB** stores time series as models (constant or linear) per segment within a user-set error bound.
- **Dual prediction** in sensor networks (Le Borgne, Santini, Bontempi, 2007) is what the robots already do.

What this project adds is the chain: prediction on the robot, error-bounded segments on the server, one error budget across both, and the measurement below against the database's own compression rather than against raw rows.

## Measured

40 simulated robots, 3 hours (92,484 keyframe rows), each stored both ways in a TimescaleDB hypertable with the same compression settings:

| | Keyframe rows | Line segments | Saving |
| :--- | :--- | :--- | :--- |
| Uncompressed | 18.7 MB | 11.6 MB | 38% |
| Compressed (what is on disk after a week) | 7.2 MB | 3.3 MB | **54%** |

Zero verification failures over the run. Counting stored numbers instead of bytes, a larger server tolerance saves more: 62% fewer numbers at 1x the robot's tolerance, 74% at 2x, 80% at 3x.

The saving is smaller than an early estimate (86%) that fitted lines through the stored points only. Following the prediction between them is what the guarantee needs, and the stored path has a jump of about one tolerance at almost every keyframe, because that is exactly when a robot sends one. Dual prediction had already removed the predictable part.

## Around the segments

- **Status and mission** are stored as their changes (`telemetry_states`): each hour's value in force at its start, then each change, and NULL where it becomes unknown.
- **Reading.** `GET /api/v1/robots/<id>/series?field=&start=&end=` returns a field's segments (or changes) with its error bound: stored ones for finished hours, fitted from raw keyframes on the fly for the hour in progress and for any finished hour with nothing stored. `/track` rebuilds a route from the position and battery segments once its raw keyframes are gone.
- **Raw retention** (`app/storage/retention.py`) replaces the fixed 30-day policy. Raw keyframes are dropped only when older than `RAW_RETENTION_DAYS`, only for hours the segments job has finished, and never for an hour with a segment that failed verification (saved, and cleared only by a full redo that passes). Before dropping, the keyframes within `INCIDENT_MARGIN_MINUTES` of any alarm on the same robot are copied to `telemetry_incidents`, kept a year, so incidents keep their exact data.

Not built: precision that falls with age, or that follows importance beyond keeping alarm data raw; curves for position (measured 25% fewer pieces than lines for x and y); the minute and hour rollups still read raw keyframes, so they are computed before raw data goes.

## Consequences

- About half the disk per robot for long-term history, at the same error bound; more with a larger tolerance.
- Values from segments are approximations within a known bound, which every reading must report, as live predictions already do.
- One more background job: 2.3 s of CPU per hour for 40 robots (30,456 keyframes into 40,295 segments), about 6 s of wall time (`fleetops_lines_run_seconds`). The fitting runs in a worker thread and rows are read and written in batches, so the API's event loop is held for at most about 50 ms at a time (1.9 s when the fitting first ran on the loop).
