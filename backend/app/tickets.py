"""Short-lived, scoped tickets for browser clients.

The dashboard used to be handed the master ``TELEMETRY_API_KEY`` at build time.
That key authorizes telemetry *ingest* for the entire fleet, and Vite compiles
build-time variables straight into the bundle, so anyone who opened devtools on
the public demo could read a long-lived credential and inject readings for any
robot forever.

A ticket replaces it. The browser requests one per page load and it:

* **expires** — minutes, not forever, so a scraped ticket is worthless shortly
  after it is taken;
* **cannot ingest** — the ingest routes still require the master key, which now
  never leaves the server and the simulator;
* **carries an explicit scope**, signed alongside the expiry, so a client cannot
  widen its own permissions or extend its own lifetime.

Format::

    v1.<scope>.<expires_at>.<nonce>.<signature>

Signed with HMAC-SHA256 keyed on the master API key. Deriving the signing key
rather than adding a second secret means there is nothing extra to configure or
rotate, and rotating the API key invalidates every outstanding ticket — which
is the behaviour you want from a rotation.

This is a blast-radius reduction, not user authentication. The demo has no
per-user identity, so the ticket endpoint is as public as the read endpoints it
sits alongside (both honour ``REQUIRE_AUTH_FOR_READS``). What it buys is that a
browser credential can no longer forge telemetry and stops working on its own.
"""

import hmac
import logging
import secrets
import time
from hashlib import sha256

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# The operator console: read the live WebSocket feed and dispatch commands.
SCOPE_CONSOLE = "console"

_VERSION = "v1"
_FIELD_SEP = "."


def _sign(payload: str) -> str:
    """HMAC-SHA256 of the payload, keyed on the master API key."""
    return hmac.new(
        settings.telemetry_api_key.encode("utf-8"),
        payload.encode("utf-8"),
        sha256,
    ).hexdigest()


def issue_ticket(scope: str = SCOPE_CONSOLE, ttl_seconds: int | None = None) -> tuple[str, int]:
    """Mint a ticket for ``scope``. Returns ``(ticket, expires_at_unix)``."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.ticket_ttl_seconds
    expires_at = int(time.time()) + ttl
    # A nonce makes tickets issued in the same second distinguishable, so one
    # appearing twice in a log is a replay rather than a coincidence.
    nonce = secrets.token_urlsafe(9)
    payload = _FIELD_SEP.join((_VERSION, scope, str(expires_at), nonce))
    return f"{payload}{_FIELD_SEP}{_sign(payload)}", expires_at


def verify_ticket(ticket: str | None, scope: str = SCOPE_CONSOLE) -> bool:
    """Validate signature, expiry, and scope. False on anything malformed.

    Order matters: the signature is checked before the expiry is trusted,
    because the expiry is attacker-supplied until the HMAC says otherwise.
    """
    if not ticket:
        return False

    parts = ticket.split(_FIELD_SEP)
    if len(parts) != 5:
        return False

    version, ticket_scope, expires_raw, _nonce, signature = parts
    if version != _VERSION:
        return False

    payload = _FIELD_SEP.join(parts[:4])
    if not hmac.compare_digest(signature, _sign(payload)):
        return False

    # Signature verified, so the remaining fields can now be trusted.
    if not hmac.compare_digest(ticket_scope, scope):
        return False

    try:
        expires_at = int(expires_raw)
    except ValueError:
        return False

    return time.time() < expires_at
