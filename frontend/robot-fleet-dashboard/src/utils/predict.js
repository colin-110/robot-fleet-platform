// The shared predictor, in JavaScript: a copy of agent/fleetops_agent/predict.py
// that the dashboard runs between keyframes. predict.test.js checks it against
// agent/tests/predict_vectors.json, the cases every copy must agree on.
// Times are epoch seconds, as in the Python version.

export const LINEAR = { x: "vx", y: "vy", battery: "battery_rate" };

export const HELD = {
  temperature: "tol_temperature_c",
  speed: "tol_speed",
  mission_progress: "tol_progress_pct",
  battery_health: "tol_health_pct",
  motor_health: "tol_health_pct",
  sensor_health: "tol_health_pct",
  network_health: "tol_health_pct",
};

export const DISCRETE = ["status", "mission_id", "mission_type", "mission_start_time"];

export const DEFAULTS = {
  tol_position_m: 0.5,
  tol_battery_pct: 1.0,
  tol_temperature_c: 1.0,
  tol_speed: 0.3,
  tol_progress_pct: 5.0,
  tol_health_pct: 2.0,
  heartbeat_s: 30.0,
};

const isMissing = (value) => value === null || value === undefined;

export function tolerances(config = {}) {
  const merged = { ...DEFAULTS };
  for (const [key, value] of Object.entries(config ?? {})) {
    if (key in DEFAULTS && !isMissing(value)) merged[key] = Number(value);
  }
  return merged;
}

export function predict(keyframe, t) {
  const dt = Math.max(0, t - keyframe.timestamp);
  const out = { ...keyframe, timestamp: Math.max(t, keyframe.timestamp) };
  for (const [name, rate] of Object.entries(LINEAR)) {
    const value = keyframe[name];
    if (!isMissing(value)) out[name] = value + (keyframe[rate] || 0) * dt;
  }
  if (!isMissing(out.battery)) out.battery = Math.min(100, Math.max(0, out.battery));
  return out;
}

export function keyframeReason(keyframe, reading, tol) {
  if (isMissing(keyframe)) return "first";
  const t = reading.timestamp;
  if (t - keyframe.timestamp >= tol.heartbeat_s) return "heartbeat";
  for (const name of DISCRETE) {
    if ((reading[name] ?? null) !== (keyframe[name] ?? null)) return name;
  }
  const expected = predict(keyframe, t);
  const names = ["x", "y", "battery", ...Object.keys(HELD)];
  if (names.some((n) => isMissing(expected[n]) !== isMissing(reading[n]))) return "field";
  if (!isMissing(reading.x) && !isMissing(reading.y)) {
    const error = Math.hypot(reading.x - expected.x, reading.y - expected.y);
    if (error > tol.tol_position_m) return "position";
  }
  if (!isMissing(reading.battery) && Math.abs(reading.battery - expected.battery) > tol.tol_battery_pct) {
    return "battery";
  }
  for (const [name, key] of Object.entries(HELD)) {
    const value = reading[name];
    if (!isMissing(value) && Math.abs(value - expected[name]) > tol[key]) return name;
  }
  return null;
}

// Where the dashboard should draw a robot at ``nowMs``: its last keyframe
// carried forward, stopping at the heartbeat as the server does. Returns
// the robot unchanged when it has no rates or is offline.
export function carryForward(robot, nowMs, config) {
  const hasRates = ["vx", "vy", "battery_rate"].some((r) => !isMissing(robot[r]));
  const at = Date.parse(robot.keyframe_at ?? robot.timestamp ?? "");
  if (!hasRates || Number.isNaN(at) || robot.status === "OFFLINE") return robot;
  const tol = tolerances(config);
  const t0 = at / 1000;
  const until = Math.min(nowMs / 1000, t0 + tol.heartbeat_s);
  const p = predict(
    {
      timestamp: t0,
      x: robot.keyframe_x ?? robot.x,
      y: robot.keyframe_y ?? robot.y,
      battery: robot.keyframe_battery ?? robot.battery,
      vx: robot.vx,
      vy: robot.vy,
      battery_rate: robot.battery_rate,
    },
    until,
  );
  return { ...robot, x: p.x, y: p.y, battery: p.battery };
}
