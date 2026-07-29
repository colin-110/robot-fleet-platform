"""API key authentication."""

import secrets

from fastapi import Header, HTTPException

from app.config import get_settings
from app.tickets import SCOPE_CONSOLE, verify_ticket

settings = get_settings()


def is_valid_api_key(candidate: str | None) -> bool:
    """Constant-time API key comparison.

    ``==`` on strings short-circuits at the first differing byte, so response
    time leaks how many leading characters a guess got right — enough to
    recover a key byte by byte over many requests. ``compare_digest`` always
    scans the full length.
    """
    if not candidate:
        return False
    return secrets.compare_digest(candidate, settings.telemetry_api_key)


async def verify_api_key(x_api_key: str = Header(None)):
    """FastAPI dependency: reject requests without a valid API key."""
    if not is_valid_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API Key")


async def verify_console_access(
    x_api_key: str = Header(None),
    x_console_ticket: str = Header(None),
):
    """Auth for operator-console actions such as command dispatch.

    Accepts the master key (server-side callers: the simulator, load harnesses)
    or a short-lived console ticket (the dashboard). The browser therefore never
    needs the key that authorizes telemetry ingest — see ``app/tickets.py``.
    """
    if is_valid_api_key(x_api_key):
        return
    if verify_ticket(x_console_ticket, SCOPE_CONSOLE):
        return
    raise HTTPException(status_code=401, detail="Invalid API key or console ticket")


async def verify_read_access(x_api_key: str = Header(None)):
    """Auth for read-only endpoints, gated by ``REQUIRE_AUTH_FOR_READS``.

    The hosted demo serves fleet status and analytics publicly so the dashboard
    works without shipping a key to every browser. That is a deliberate choice
    for a demo with synthetic data, not an oversight — but it is the wrong
    default for real fleet positions, so it is a setting rather than a
    hardcoded exemption.
    """
    if not settings.require_auth_for_reads:
        return
    if not is_valid_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API Key")
