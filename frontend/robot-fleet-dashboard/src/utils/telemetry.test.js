import { describe, expect, it } from "vitest";
import { isNewerReading, readingTime } from "./telemetry";

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
