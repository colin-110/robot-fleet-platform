"""API key authentication."""

import secrets

from fastapi import Header, HTTPException

from app.config import get_settings

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
