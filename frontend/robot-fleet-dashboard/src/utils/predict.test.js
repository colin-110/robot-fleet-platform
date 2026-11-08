import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import { carryForward, keyframeReason, predict, tolerances } from "./predict";

// Vitest runs from the dashboard's directory (under jsdom, import.meta.url is
// not a file URL, so the path is resolved from there).
const VECTORS = JSON.parse(
  readFileSync(resolve(process.cwd(), "../../agent/tests/predict_vectors.json"), "utf8"),
);

describe("predict matches the Python predictor", () => {
  it.each(VECTORS.predictions.map((c) => [c.t, c]))("at t=%s", (_t, c) => {
    const got = predict(c.keyframe, c.t);
    expect(Object.keys(got).sort()).toEqual(Object.keys(c.expected).sort());
    for (const [key, value] of Object.entries(c.expected)) {
      if (typeof value === "number") expect(got[key]).toBeCloseTo(value, 9);
      else expect(got[key]).toEqual(value);
    }
  });
});

describe("keyframeReason matches the Python predictor", () => {
  it.each(VECTORS.reasons.map((c) => [c.name, c]))("%s", (_name, c) => {
    expect(keyframeReason(c.keyframe, c.reading, VECTORS.tolerances)).toBe(c.expected);
  });
});

describe("tolerances", () => {
  it("overrides only known keys", () => {
    const tol = tolerances({ tol_position_m: 2, report_interval_s: 1 });
    expect(tol.tol_position_m).toBe(2);
    expect(tol.heartbeat_s).toBe(30);
    expect(tol.report_interval_s).toBeUndefined();
  });
});

describe("carryForward", () => {
  const robot = {
    robot_id: 1,
    status: "ACTIVE",
    keyframe_at: "2026-11-01T12:00:00Z",
    x: 0,
    y: 0,
    battery: 50,
    vx: 1,
    vy: 0.5,
    battery_rate: -0.1,
  };
  const at = (s) => Date.parse("2026-11-01T12:00:00Z") + s * 1000;

  it("moves the robot along its velocity", () => {
    const p = carryForward(robot, at(10));
    expect(p.x).toBeCloseTo(10);
    expect(p.y).toBeCloseTo(5);
    expect(p.battery).toBeCloseTo(49);
  });

  it("stops at the heartbeat", () => {
    expect(carryForward(robot, at(100), { heartbeat_s: 20 }).x).toBeCloseTo(20);
  });

  it("leaves robots without rates or offline alone", () => {
    const still = { ...robot, vx: null, vy: null, battery_rate: null };
    expect(carryForward(still, at(10))).toBe(still);
    const offline = { ...robot, status: "OFFLINE" };
    expect(carryForward(offline, at(10))).toBe(offline);
  });
});
