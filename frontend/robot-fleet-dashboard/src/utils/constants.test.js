import { describe, it, expect, vi, afterEach } from "vitest";
import {
  getStatusMeta,
  healthTone,
  formatRuntime,
  formatLastSeen,
  STATUS_META,
} from "./constants";

afterEach(() => {
  vi.useRealTimers();
});

describe("getStatusMeta", () => {
  it("returns the entry for a known status", () => {
    expect(getStatusMeta("OVERHEATING")).toBe(STATUS_META.OVERHEATING);
    expect(getStatusMeta("LOW POWER")).toBe(STATUS_META["LOW POWER"]);
  });

  it("falls back to ACTIVE for an unknown or missing status", () => {
    expect(getStatusMeta("NOT_A_STATUS")).toBe(STATUS_META.ACTIVE);
    expect(getStatusMeta(undefined)).toBe(STATUS_META.ACTIVE);
  });
});

describe("healthTone", () => {
  it("banks at the documented thresholds", () => {
    expect(healthTone(0)).toBe("hsl(var(--bad))");
    expect(healthTone(60)).toBe("hsl(var(--bad))");
    expect(healthTone(60.1)).toBe("hsl(var(--warn))");
    expect(healthTone(80)).toBe("hsl(var(--warn))");
    expect(healthTone(80.1)).toBe("hsl(var(--good))");
    expect(healthTone(100)).toBe("hsl(var(--good))");
  });
});

describe("formatRuntime", () => {
  it("reports minutes below an hour and hours above it", () => {
    expect(formatRuntime(0)).toBe("0.0 min");
    expect(formatRuntime(42.35)).toBe("42.4 min");
    expect(formatRuntime(59.9)).toBe("59.9 min");
    expect(formatRuntime(60)).toBe("1.0 h");
    expect(formatRuntime(150)).toBe("2.5 h");
  });

  // The API returns null for a robot with no battery estimate; "n/a" must not
  // become "null min".
  it("renders absent values as n/a", () => {
    expect(formatRuntime(null)).toBe("n/a");
    expect(formatRuntime(undefined)).toBe("n/a");
  });
});

describe("formatLastSeen", () => {
  it("scales the unit with the age of the reading", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-29T12:00:00Z"));

    expect(formatLastSeen("2026-07-29T11:59:31Z")).toBe("29s ago");
    expect(formatLastSeen("2026-07-29T11:55:00Z")).toBe("5m ago");
    expect(formatLastSeen("2026-07-29T09:00:00Z")).toBe("3h ago");
  });

  // Clock skew between the device and the browser can date a reading in the
  // future; "-4s ago" would look like a bug in the dashboard.
  it("clamps future timestamps to zero rather than going negative", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-29T12:00:00Z"));

    expect(formatLastSeen("2026-07-29T12:00:04Z")).toBe("0s ago");
  });

  it("rejects missing and unparseable timestamps", () => {
    expect(formatLastSeen(null)).toBe("n/a");
    expect(formatLastSeen("")).toBe("n/a");
    expect(formatLastSeen("not-a-date")).toBe("n/a");
  });
});
