import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  authHeaders,
  fetchAuthConfig,
  getSession,
  login,
  logout,
  onSessionChange,
  restoreSession,
  _resetSession,
} from "./auth";

const STORAGE_KEY = "fleet.session";
const futureExp = () => Math.floor(Date.now() / 1000) + 3600;

const loginResponse = (overrides = {}) => ({
  ok: true,
  json: async () => ({
    access_token: "token-abc",
    token_type: "bearer",
    expires_at: futureExp(),
    username: "alice",
    role: "operator",
    ...overrides,
  }),
});

beforeEach(() => {
  _resetSession();
  sessionStorage.clear();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("login", () => {
  it("posts credentials and stores the session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(loginResponse());
    vi.stubGlobal("fetch", fetchMock);

    const session = await login("alice", "s3cret");

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "alice", password: "s3cret" }),
    });
    expect(session.username).toBe("alice");
    expect(session.role).toBe("operator");
  });

  it("surfaces the backend's message verbatim", async () => {
    // The backend returns one message for both "no such user" and "wrong
    // password"; rewording it here would leak the distinction back.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Invalid username or password" }),
      }),
    );

    await expect(login("alice", "wrong")).rejects.toThrow("Invalid username or password");
    expect(getSession()).toBeNull();
  });

  it("still reports a failure when the error body is unreadable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error("not json");
        },
      }),
    );

    await expect(login("alice", "s3cret")).rejects.toThrow("Sign-in failed");
  });
});

describe("authHeaders", () => {
  it("is empty with no session, so open mode sends nothing", () => {
    expect(authHeaders()).toEqual({});
  });

  it("carries the bearer token once signed in", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(loginResponse()));
    await login("alice", "s3cret");

    expect(authHeaders()).toEqual({ Authorization: "Bearer token-abc" });
  });
});

describe("session lifetime", () => {
  it("treats an expired session as no session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(loginResponse({ expires_at: Math.floor(Date.now() / 1000) + 60 })),
    );
    await login("alice", "s3cret");
    expect(getSession()).not.toBeNull();

    vi.useFakeTimers();
    vi.setSystemTime(Date.now() + 120_000);

    expect(getSession()).toBeNull();
    expect(authHeaders()).toEqual({});
  });

  it("clears the session on logout", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(loginResponse()));
    await login("alice", "s3cret");

    logout();

    expect(getSession()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("notifies subscribers on sign-in and sign-out", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(loginResponse()));
    const seen = [];
    onSessionChange((s) => seen.push(s?.username ?? null));

    await login("alice", "s3cret");
    logout();

    expect(seen).toEqual(["alice", null]);
  });
});

describe("restoreSession", () => {
  it("reloads a session left by a previous page load", () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ token: "t", username: "bob", role: "viewer", expiresAt: futureExp() }),
    );

    const restored = restoreSession();

    expect(restored.username).toBe("bob");
    expect(authHeaders()).toEqual({ Authorization: "Bearer t" });
  });

  it("discards an expired stored session rather than sending a dead token", () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ token: "t", username: "bob", role: "viewer", expiresAt: 1 }),
    );

    expect(restoreSession()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("survives corrupt storage", () => {
    sessionStorage.setItem(STORAGE_KEY, "{not json");

    expect(restoreSession()).toBeNull();
  });
});

describe("fetchAuthConfig", () => {
  it("reads the deployment's auth mode", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ auth_mode: "required", login_required: true }),
      }),
    );

    await expect(fetchAuthConfig()).resolves.toEqual({
      auth_mode: "required",
      login_required: true,
    });
  });

  it("rejects on a non-OK response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));

    await expect(fetchAuthConfig()).rejects.toThrow("503");
  });
});
