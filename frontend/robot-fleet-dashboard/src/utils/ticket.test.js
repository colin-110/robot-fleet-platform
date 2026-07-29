import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getTicket, resetTicketCache } from "./ticket";

const nowSeconds = () => Math.floor(Date.now() / 1000);

/** A ticket response expiring `ttl` seconds from now. */
const ticketResponse = (ticket, ttl = 300) => ({
  ok: true,
  json: async () => ({ ticket, expires_at: nowSeconds() + ttl, scope: "console" }),
});

beforeEach(() => {
  resetTicketCache();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("getTicket", () => {
  it("requests a ticket from the versioned endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ticketResponse("t-1"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTicket()).resolves.toBe("t-1");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/ticket", { method: "POST" });
  });

  it("reuses a cached ticket that is still fresh", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ticketResponse("t-1"));
    vi.stubGlobal("fetch", fetchMock);

    await getTicket();
    await getTicket();
    await getTicket();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // The socket opens and the command buttons render in the same tick; two
  // independent fetches for the same credential is wasted work.
  it("shares one in-flight request between concurrent callers", async () => {
    let release;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise((resolve) => { release = () => resolve(ticketResponse("t-1")); }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const both = Promise.all([getTicket(), getTicket()]);
    release();

    await expect(both).resolves.toEqual(["t-1", "t-1"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // Refreshing early avoids a request that races the expiry boundary and fails
  // for no reason the operator could act on.
  it("refetches once the cached ticket is inside the refresh margin", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ticketResponse("t-1", 40))
      .mockResolvedValueOnce(ticketResponse("t-2", 300));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTicket()).resolves.toBe("t-1");

    // 20s later the first ticket has 20s left — inside the 30s margin.
    vi.useFakeTimers();
    vi.setSystemTime(Date.now() + 20_000);

    await expect(getTicket()).resolves.toBe("t-2");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects on a non-OK response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));

    await expect(getTicket()).rejects.toThrow("401");
  });

  // A failure must not wedge every later caller onto the same rejected promise.
  it("recovers on the next call after a failure", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(ticketResponse("t-2"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getTicket()).rejects.toThrow("network down");
    await expect(getTicket()).resolves.toBe("t-2");
  });
});
