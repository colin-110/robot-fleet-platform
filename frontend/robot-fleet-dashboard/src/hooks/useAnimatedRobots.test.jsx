import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import useAnimatedRobots from "./useAnimatedRobots";

const T0 = Date.parse("2026-11-01T12:00:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
});
afterEach(() => vi.useRealTimers());

describe("useAnimatedRobots", () => {
  it("moves a robot along its predicted path as time passes", () => {
    const robots = [
      {
        robot_id: 1,
        status: "ACTIVE",
        keyframe_at: "2026-11-01T12:00:00Z",
        keyframe_x: 0,
        keyframe_y: 0,
        keyframe_battery: 50,
        x: 0,
        y: 0,
        battery: 50,
        vx: 2,
        vy: 0,
        battery_rate: -0.1,
        bounds: { heartbeat_s: 30 },
      },
    ];
    const { result } = renderHook(() => useAnimatedRobots(robots, 250));
    expect(result.current[0].x).toBeCloseTo(0);
    act(() => vi.advanceTimersByTime(5000));
    expect(result.current[0].x).toBeCloseTo(10);
    expect(result.current[0].battery).toBeCloseTo(49.5);
    // Held at the heartbeat, as the server does.
    act(() => vi.advanceTimersByTime(60000));
    expect(result.current[0].x).toBeCloseTo(60);
  });
});
