import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useWebSocket } from "./useWebSocket";

/**
 * Minimal WebSocket stand-in. jsdom has no WebSocket implementation, and a real
 * one would make these tests depend on a running backend.
 */
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.OPEN;
    this.sent = [];
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    MockWebSocket.instances.push(this);
  }

  send(payload) {
    this.sent.push(payload);
  }

  close() {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  // ── Test helpers ──
  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  emit(data) {
    this.onmessage?.({ data: typeof data === "string" ? data : JSON.stringify(data) });
  }
}

const latest = () => MockWebSocket.instances.at(-1);

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useWebSocket", () => {
  it("appends the api_key parameter to the handshake URL", () => {
    renderHook(() => useWebSocket("ws://localhost:8000/ws", () => {}));

    expect(latest().url).toBe("ws://localhost:8000/ws?api_key=test-api-key");
  });

  it("uses & when the URL already carries a query string", () => {
    renderHook(() => useWebSocket("ws://localhost:8000/ws?fleet=a", () => {}));

    expect(latest().url).toBe("ws://localhost:8000/ws?fleet=a&api_key=test-api-key");
  });

  it("reports connection state from the socket lifecycle", async () => {
    const { result } = renderHook(() => useWebSocket("ws://x/ws", () => {}));
    expect(result.current.isConnected).toBe(false);

    act(() => latest().open());
    await waitFor(() => expect(result.current.isConnected).toBe(true));

    act(() => latest().close());
    await waitFor(() => expect(result.current.isConnected).toBe(false));
  });

  it("parses JSON frames and forwards them to the handler", () => {
    const onMessage = vi.fn();
    renderHook(() => useWebSocket("ws://x/ws", onMessage));

    act(() => latest().emit({ robot_id: 3, battery: 88.5 }));

    expect(onMessage).toHaveBeenCalledWith({ robot_id: 3, battery: 88.5 });
  });

  it("ignores heartbeat frames and malformed payloads without throwing", () => {
    const onMessage = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    renderHook(() => useWebSocket("ws://x/ws", onMessage));

    act(() => {
      latest().emit("ping");
      latest().emit("pong");
      latest().emit("{not json");
    });

    expect(onMessage).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledTimes(1); // only the malformed frame
  });

  it("calls the latest handler without reopening the socket", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ cb }) => useWebSocket("ws://x/ws", cb), {
      initialProps: { cb: first },
    });

    rerender({ cb: second });
    act(() => latest().emit({ robot_id: 1 }));

    expect(MockWebSocket.instances).toHaveLength(1); // not reconnected
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith({ robot_id: 1 });
  });

  it("reconnects three seconds after an unexpected close", () => {
    vi.useFakeTimers();
    renderHook(() => useWebSocket("ws://x/ws", () => {}));

    act(() => latest().close());
    expect(MockWebSocket.instances).toHaveLength(1);

    act(() => vi.advanceTimersByTime(3000));
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  // The leak that matters: a component unmounts, its socket closes, and the
  // close handler schedules a reconnect that resurrects a dead component.
  it("does not reconnect after unmount", () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useWebSocket("ws://x/ws", () => {}));

    act(() => unmount());
    act(() => vi.advanceTimersByTime(30000));

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(latest().readyState).toBe(MockWebSocket.CLOSED);
  });

  it("stops the heartbeat once the socket is gone", () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useWebSocket("ws://x/ws", () => {}));
    const socket = latest();

    act(() => socket.open());
    act(() => vi.advanceTimersByTime(15000));
    expect(socket.sent).toEqual(["ping"]);

    act(() => unmount());
    act(() => vi.advanceTimersByTime(60000));
    expect(socket.sent).toEqual(["ping"]); // no further heartbeats
  });
});
