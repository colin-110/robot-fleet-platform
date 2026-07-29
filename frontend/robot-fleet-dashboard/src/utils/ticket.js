/**
 * Console ticket acquisition.
 *
 * The dashboard used to be built with the master API key inlined
 * (`VITE_WS_API_KEY`), which put a credential that authorizes fleet-wide
 * telemetry ingest into the JavaScript bundle. It now asks the backend for a
 * short-lived, console-scoped ticket instead — see `backend/app/tickets.py`.
 *
 * The cache is module-level on purpose: the socket hook and command dispatch
 * both need a ticket, and without sharing one they would each mint their own
 * on every page load.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

// Refresh this many seconds before expiry rather than waiting to be rejected —
// a request that races the expiry boundary would otherwise fail for no reason
// the operator could act on.
const REFRESH_MARGIN_SECONDS = 30;

let cached = null; // { ticket, expiresAt }
let inflight = null;

function isFresh(entry) {
  if (!entry) return false;
  return Date.now() / 1000 < entry.expiresAt - REFRESH_MARGIN_SECONDS;
}

/**
 * Return a valid console ticket, fetching one if the cached ticket is missing
 * or close to expiry.
 *
 * Concurrent callers share a single in-flight request: the dashboard opens its
 * socket and renders command buttons in the same tick, and two independent
 * fetches for the same credential is wasted work.
 *
 * @returns {Promise<string>} the ticket
 */
export async function getTicket() {
  if (isFresh(cached)) return cached.ticket;
  if (inflight) return inflight;

  inflight = (async () => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/auth/ticket`, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Ticket request failed: ${response.status}`);
      }
      const data = await response.json();
      cached = { ticket: data.ticket, expiresAt: data.expires_at };
      return cached.ticket;
    } finally {
      // Clear regardless of outcome, so a failed fetch doesn't wedge every
      // later caller onto the same rejected promise.
      inflight = null;
    }
  })();

  return inflight;
}

/** Drop the cached ticket. Exported for tests. */
export function resetTicketCache() {
  cached = null;
  inflight = null;
}
