import { describe, expect, it } from "vitest";
import { applyPresence, asKeyframe, isNewerReading, readingTime } from "./telemetry";

describe("readingTime", () => {
  it("prefers the live timestamp over last_seen", () => {
    const item = { timestamp: "2026-11-01T12:00:05Z", last_seen: "2026-11-01T12:00:00Z" };
    expect(readingTime(item)).toBe(Date.parse("2026-11-01T12:00:05Z"));
  });

  it("falls back to last_seen for a REST snapshot", () => {
    expect(readingTime({ last_seen: "2026-11-01T12:00:00Z" })).toBe(
      Date.parse("2026-11-01T12:00:00Z"),
    );
  });

  it("treats a missing or invalid time as the oldest possible", () => {
    expect(readingTime({})).toBe(0);
    expect(readingTime({ timestamp: "not a date" })).toBe(0);
  });
});

describe("isNewerReading", () => {
  const at = (iso) => ({ timestamp: iso });

  it("accepts a newer reading", () => {
    expect(isNewerReading(at("2026-11-01T12:00:05Z"), at("2026-11-01T12:00:00Z"))).toBe(true);
  });

  it("rejects an older, resent reading", () => {
    expect(isNewerReading(at("2026-11-01T12:00:00Z"), at("2026-11-01T12:00:05Z"))).toBe(false);
  });

  it("accepts the same reading again", () => {
    expect(isNewerReading(at("2026-11-01T12:00:00Z"), at("2026-11-01T12:00:00Z"))).toBe(true);
  });

  it("accepts anything when there is no current state", () => {
    expect(isNewerReading(at("2026-11-01T12:00:00Z"), undefined)).toBe(true);
  });

  it("compares a live reading against a snapshot's last_seen", () => {
    const snapshot = { last_seen: "2026-11-01T12:00:05Z" };
    expect(isNewerReading(at("2026-11-01T12:00:00Z"), snapshot)).toBe(false);
  });
});

describe("applyPresence", () => {
  const robots = [
    { robot_id: 1, status: "ACTIVE" },
    { robot_id: 2, status: "CHARGING" },
  ];

  it("marks a disconnected robot offline at once", () => {
    const next = applyPresence(robots, { 2: false });
    expect(next.map((r) => r.status)).toEqual(["ACTIVE", "OFFLINE"]);
  });

  it("leaves robots alone when they come online", () => {
    expect(applyPresence(robots, { 1: true })).toBe(robots);
  });

  it("does not mutate the input", () => {
    applyPresence(robots, { 1: false });
    expect(robots[0].status).toBe("ACTIVE");
  });
});

describe("asKeyframe", () => {
  it("makes the message's values the keyframe the predictor starts from", () => {
    const kf = asKeyframe({ robot_id: 1, x: 3, y: 4, battery: 70, timestamp: "2026-11-01T12:00:00Z" });
    expect(kf).toMatchObject({ keyframe_x: 3, keyframe_y: 4, keyframe_battery: 70, keyframe_at: "2026-11-01T12:00:00Z" });
  });
});
