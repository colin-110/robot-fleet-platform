import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useWebSocket } from "./useWebSocket";
import { getTicket } from "../utils/ticket";

vi.mock("../utils/ticket", () => ({
  getTicket: vi.fn(() => Promise.resolve("v1.console.9999999999.abc.sig")),
}));

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

/** The socket is opened only after the ticket request resolves. */
const waitForSocket = (count = 1) =>
  waitFor(() => expect(MockWebSocket.instances).toHaveLength(count));

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.mocked(getTicket).mockResolvedValue("v1.console.9999999999.abc.sig");
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useWebSocket", () => {
  it("authenticates the handshake with a console ticket, not the API key", async () => {
    renderHook(() => useWebSocket("ws://localhost:8000/ws", () => {}));
    await waitForSocket();

    expect(latest().url).toBe(
      "ws://localhost:8000/ws?ticket=v1.console.9999999999.abc.sig",
    );
    // The master key must never appear in a handshake from the browser.
    expect(latest().url).not.toContain("api_key");
  });

  it("percent-encodes the ticket", async () => {
    vi.mocked(getTicket).mockResolvedValue("tick et/with+chars");
    renderHook(() => useWebSocket("ws://x/ws", () => {}));
    await waitForSocket();

    expect(latest().url).toBe("ws://x/ws?ticket=tick%20et%2Fwith%2Bchars");
  });

  it("uses & when the URL already carries a query string", async () => {
    renderHook(() => useWebSocket("ws://localhost:8000/ws?fleet=a", () => {}));
    await waitForSocket();

    expect(latest().url).toBe(
      "ws://localhost:8000/ws?fleet=a&ticket=v1.console.9999999999.abc.sig",
    );
  });

  it("reports connection state from the socket lifecycle", async () => {
    const { result } = renderHook(() => useWebSocket("ws://x/ws", () => {}));
    await waitForSocket();
    expect(result.current.isConnected).toBe(false);

    act(() => latest().open());
    await waitFor(() => expect(result.current.isConnected).toBe(true));

    act(() => latest().close());
    await waitFor(() => expect(result.current.isConnected).toBe(false));
  });

  it("parses JSON frames and forwards them to the handler", async () => {
    const onMessage = vi.fn();
    renderHook(() => useWebSocket("ws://x/ws", onMessage));
    await waitForSocket();

    act(() => latest().emit({ robot_id: 3, battery: 88.5 }));

    expect(onMessage).toHaveBeenCalledWith({ robot_id: 3, battery: 88.5 });
  });

  it("ignores heartbeat frames and malformed payloads without throwing", async () => {
    const onMessage = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    renderHook(() => useWebSocket("ws://x/ws", onMessage));
    await waitForSocket();

    act(() => {
      latest().emit("ping");
      latest().emit("pong");
      latest().emit("{not json");
    });

    expect(onMessage).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledTimes(1); // only the malformed frame
  });

  it("calls the latest handler without reopening the socket", async () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ cb }) => useWebSocket("ws://x/ws", cb), {
      initialProps: { cb: first },
    });
    await waitForSocket();

    rerender({ cb: second });
    act(() => latest().emit({ robot_id: 1 }));

    expect(MockWebSocket.instances).toHaveLength(1); // not reconnected
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith({ robot_id: 1 });
  });

  it("reconnects with a freshly fetched ticket after an unexpected close", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderHook(() => useWebSocket("ws://x/ws", () => {}));
    await waitForSocket();

    act(() => latest().close());
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });

    await waitForSocket(2);
    // A reconnect must not reuse a ticket that may have expired meanwhile.
    expect(getTicket).toHaveBeenCalledTimes(2);
  });

  // The leak that matters: a component unmounts, its socket closes, and the
  // close handler schedules a reconnect that resurrects a dead component.
  it("does not reconnect after unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { unmount } = renderHook(() => useWebSocket("ws://x/ws", () => {}));
    await waitForSocket();

    act(() => unmount());
    await act(async () => {
      vi.advanceTimersByTime(30000);
    });

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(latest().readyState).toBe(MockWebSocket.CLOSED);
  });

  // Unmounting mid-request is the window the async ticket fetch opened up: the
  // cleanup has already run and has no socket to close yet.
  it("does not open a socket if unmounted while the ticket is in flight", async () => {
    let releaseTicket;
    vi.mocked(getTicket).mockReturnValue(
      new Promise((resolve) => { releaseTicket = resolve; }),
    );
    const { unmount } = renderHook(() => useWebSocket("ws://x/ws", () => {}));

    unmount();
    await act(async () => {
      releaseTicket("late-ticket");
    });

    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it("retries rather than giving up when the ticket request fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(getTicket).mockRejectedValueOnce(new Error("503"));

    renderHook(() => useWebSocket("ws://x/ws", () => {}));
    await waitFor(() => expect(getTicket).toHaveBeenCalledTimes(1));
    expect(MockWebSocket.instances).toHaveLength(0);

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    await waitForSocket();
  });

  it("stops the heartbeat once the socket is gone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { unmount } = renderHook(() => useWebSocket("ws://x/ws", () => {}));
    await waitForSocket();
    const socket = latest();

    act(() => socket.open());
    act(() => vi.advanceTimersByTime(15000));
    expect(socket.sent).toEqual(["ping"]);

    act(() => unmount());
    act(() => vi.advanceTimersByTime(60000));
    expect(socket.sent).toEqual(["ping"]); // no further heartbeats
  });
});
