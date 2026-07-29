/**
 * Session handling for the operator console.
 *
 * The backend runs in one of two modes and the dashboard has to work against
 * either without a rebuild, so it asks (`GET /api/v1/auth/config`) rather than
 * assuming. In `open` mode there is no login and nothing here does anything;
 * in `required` mode every request carries a bearer token.
 *
 * The token lives in `sessionStorage`, not `localStorage`: it is scoped to the
 * tab and cleared when the tab closes, so a shared machine does not leave an
 * operator signed in indefinitely. Neither survives XSS — the real mitigation
 * for that is the short token lifetime and the fact that the master API key is
 * no longer in the bundle at all.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
const STORAGE_KEY = "fleet.session";

let session = null; // { token, username, role, expiresAt }
const listeners = new Set();

function notify() {
  for (const listener of listeners) listener(session);
}

/** Subscribe to sign-in/sign-out. Returns an unsubscribe function. */
export function onSessionChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function isExpired(candidate) {
  if (!candidate?.expiresAt) return true;
  return Date.now() / 1000 >= candidate.expiresAt;
}

function persist(next) {
  session = next;
  try {
    if (next) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private browsing can throw on write. An in-memory session still works
    // for this tab, so this is not worth failing a sign-in over.
  }
  notify();
}

/** Reload a session saved by a previous page load, if it is still valid. */
export function restoreSession() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (isExpired(parsed)) {
      sessionStorage.removeItem(STORAGE_KEY);
      return null;
    }
    session = parsed;
    return session;
  } catch {
    return null;
  }
}

export function getSession() {
  if (session && isExpired(session)) persist(null);
  return session;
}

/** Authorization header, or an empty object when there is no session. */
export function authHeaders() {
  const current = getSession();
  return current ? { Authorization: `Bearer ${current.token}` } : {};
}

/** Ask the backend whether this deployment expects a login. */
export async function fetchAuthConfig() {
  const response = await fetch(`${API_BASE}/api/v1/auth/config`);
  if (!response.ok) throw new Error(`Auth config request failed: ${response.status}`);
  return response.json();
}

export async function login(username, password) {
  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    // The backend deliberately returns one message for both "no such user"
    // and "wrong password"; surfacing it verbatim keeps that property.
    const detail = await response
      .json()
      .then((body) => body.detail)
      .catch(() => null);
    throw new Error(detail || "Sign-in failed");
  }

  const body = await response.json();
  persist({
    token: body.access_token,
    username: body.username,
    role: body.role,
    expiresAt: body.expires_at,
  });
  return getSession();
}

export function logout() {
  persist(null);
}

/** Test seam. */
export function _resetSession() {
  session = null;
  listeners.clear();
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to clear */
  }
}
