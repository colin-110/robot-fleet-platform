import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import axios from "axios";
import useFleetData from "./useFleetData";
import { EVENT_BUFFER_LIMIT } from "../utils/constants";

vi.mock("axios");

// Capture the message handler the hook registers, so tests can push frames in
// without standing up a socket.
let pushMessage;
vi.mock("./useWebSocket", () => ({
  useWebSocket: (_url, onMessage) => {
    pushMessage = onMessage;
    return { isConnected: true };
  },
}));

const robot = (id, overrides = {}) => ({
  robot_id: id,
  battery: 90,
  status: "ACTIVE",
  ...overrides,
});

const ANALYTICS = {
  fleet_health_trend: [{ t: 1, v: 2 }],
  battery_distribution: [],
  temperature_distribution: [],
  mission_completion_count: [],
  robot_status_breakdown: [],
};

/** Route axios.get by URL so tests control each endpoint independently. */
function mockEndpoints({ robots = [], analytics = ANALYTICS, events = [] } = {}) {
  axios.get.mockImplementation((url) => {
    if (url.endsWith("/robots/status")) {
      return typeof robots === "function"
        ? robots()
        : Promise.resolve({ data: robots });
    }
    if (url.endsWith("/analytics/fleet")) {
      return typeof analytics === "function"
        ? analytics()
        : Promise.resolve({ data: analytics });
    }
    if (url.endsWith("/events")) {
      return typeof events === "function"
        ? events()
        : Promise.resolve({ data: events });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  pushMessage = undefined;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useFleetData", () => {
  it("loads the fleet and analytics from the versioned API", async () => {
    mockEndpoints({ robots: [robot(1), robot(2)] });
    const { result } = renderHook(() => useFleetData());

    await waitFor(() => expect(result.current.robots).toHaveLength(2));

    expect(axios.get).toHaveBeenCalledWith(
      "/api/v1/robots/status",
      { params: { limit: 1000 } },
    );
    expect(axios.get).toHaveBeenCalledWith("/api/v1/analytics/fleet");
    await waitFor(() =>
      expect(result.current.analytics.fleet_health_trend).toHaveLength(1),
    );
  });

  // The default page size used to clip the fleet at 50 robots.
  it("requests the whole fleet rather than the default page", async () => {
    mockEndpoints({ robots: [] });
    renderHook(() => useFleetData());

    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    const call = axios.get.mock.calls.find(([url]) => url.endsWith("/robots/status"));
    expect(call[1].params.limit).toBe(1000);
  });

  it("clears isLoading and surfaces a message when the backend is down", async () => {
    mockEndpoints({ robots: () => Promise.reject(new Error("ECONNREFUSED")) });
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { result } = renderHook(() => useFleetData());

    await waitFor(() => expect(result.current.error).toBe("Backend is unreachable."));
    expect(result.current.isLoading).toBe(false);
  });

  // Analytics is the heavier query; a slow or failing one must not keep the
  // fleet roster from painting.
  it("renders the roster even when analytics fails", async () => {
    mockEndpoints({
      robots: [robot(1)],
      analytics: () => Promise.reject(new Error("timeout")),
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { result } = renderHook(() => useFleetData());

    await waitFor(() => expect(result.current.robots).toHaveLength(1));
    expect(result.current.error).toBe("");
  });

  it("applies live telemetry to the matching robot after the throttle window", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEndpoints({ robots: [robot(1, { battery: 90 })] });
    const { result } = renderHook(() => useFleetData());

    await waitFor(() => expect(result.current.robots).toHaveLength(1));

    act(() => pushMessage({ robot_id: 1, battery: 42.5, status: "LOW POWER" }));
    act(() => vi.advanceTimersByTime(100));

    await waitFor(() => expect(result.current.robots[0].battery).toBe(42.5));
    expect(result.current.robots[0].status).toBe("LOW POWER");
  });

  it("coalesces a burst of frames into one update per robot", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEndpoints({ robots: [robot(1)] });
    const { result } = renderHook(() => useFleetData());
    await waitFor(() => expect(result.current.robots).toHaveLength(1));

    act(() => {
      for (let i = 1; i <= 20; i++) pushMessage({ robot_id: 1, battery: i });
    });
    act(() => vi.advanceTimersByTime(100));

    // Last write wins; the intermediate 19 never reach React.
    await waitFor(() => expect(result.current.robots[0].battery).toBe(20));
  });

  // Command broadcasts arrive as EVENT frames with no message and would
  // otherwise flood the log with blank rows.
  it("logs only EVENT frames that carry a message", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEndpoints({ robots: [] });
    const { result } = renderHook(() => useFleetData());
    await waitFor(() => expect(axios.get).toHaveBeenCalled());

    act(() => {
      pushMessage({ type: "EVENT", message: "R3 entered restricted zone" });
      pushMessage({ type: "EVENT" });
      pushMessage({ type: "COMMAND_STATUS", command_id: 9 });
    });
    act(() => vi.advanceTimersByTime(100));

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].message).toBe("R3 entered restricted zone");
  });

  it("caps the event log at the buffer limit", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEndpoints({ robots: [] });
    const { result } = renderHook(() => useFleetData());
    await waitFor(() => expect(axios.get).toHaveBeenCalled());

    act(() => {
      for (let i = 0; i < EVENT_BUFFER_LIMIT + 20; i++) {
        pushMessage({ type: "EVENT", message: `event ${i}` });
      }
    });
    act(() => vi.advanceTimersByTime(100));

    await waitFor(() =>
      expect(result.current.events).toHaveLength(EVENT_BUFFER_LIMIT),
    );
  });

  // The feed is otherwise socket-only, so every reload started the log empty
  // and it filled from nothing — a fleet that had been running for hours
  // looked idle until the next event happened to fire.
  it("seeds the log with persisted history on mount", async () => {
    mockEndpoints({
      robots: [],
      events: [
        { id: 2, robot_id: 4, message: "Entered restricted zone", timestamp: "2026-08-08T10:00:02Z" },
        { id: 1, robot_id: 7, message: "Completed PATROL mission", timestamp: "2026-08-08T10:00:01Z" },
      ],
    });
    const { result } = renderHook(() => useFleetData());

    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(axios.get).toHaveBeenCalledWith("/api/v1/events", {
      params: { limit: EVENT_BUFFER_LIMIT },
    });
    // Newest first, and tagged as an EVENT so the renderer treats a REST row
    // and a socket frame identically.
    expect(result.current.events[0].message).toBe("Entered restricted zone");
    expect(result.current.events[0].type).toBe("EVENT");
  });

  // The backfill and the socket overlap around page load, so the same event
  // can arrive down both paths.
  it("does not show an event twice when history and the socket overlap", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const duplicate = {
      robot_id: 4,
      message: "Entered restricted zone",
      timestamp: "2026-08-08T10:00:02Z",
    };
    mockEndpoints({ robots: [], events: [{ id: 2, ...duplicate }] });
    const { result } = renderHook(() => useFleetData());
    await waitFor(() => expect(result.current.events).toHaveLength(1));

    act(() => pushMessage({ type: "EVENT", ...duplicate }));
    act(() => vi.advanceTimersByTime(100));

    await waitFor(() => expect(result.current.events).toHaveLength(1));
  });

  // Without this the WS could only ever add robots, so a retired unit would
  // linger and inflate the fleet counts forever.
  it("replaces the roster on each poll so retired robots are pruned", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let fleet = [robot(1), robot(2), robot(3)];
    mockEndpoints({ robots: () => Promise.resolve({ data: fleet }) });

    const { result } = renderHook(() => useFleetData());
    await waitFor(() => expect(result.current.robots).toHaveLength(3));

    fleet = [robot(1)];
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    await waitFor(() => expect(result.current.robots).toHaveLength(1));
    expect(result.current.robots[0].robot_id).toBe(1);
  });

  it("stops polling once unmounted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockEndpoints({ robots: [] });
    const { unmount } = renderHook(() => useFleetData());
    await waitFor(() => expect(axios.get).toHaveBeenCalled());

    const callsAtUnmount = axios.get.mock.calls.length;
    unmount();
    await act(async () => {
      vi.advanceTimersByTime(20000);
    });

    expect(axios.get.mock.calls.length).toBe(callsAtUnmount);
  });

  it("tolerates a non-array payload instead of crashing the roster", async () => {
    mockEndpoints({ robots: { detail: "Not Found" } });
    const { result } = renderHook(() => useFleetData());

    await waitFor(() => expect(result.current.error).toBe(""));
    expect(result.current.robots).toEqual([]);
  });
});
