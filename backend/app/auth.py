"""API key authentication."""

import secrets

from fastapi import Depends, Header, HTTPException

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


# ── Identity (JWT) ──────────────────────────────────────────────────


class Principal:
    """Who is making this request.

    In ``open`` mode there are no accounts, so every caller resolves to an
    anonymous operator. Routes therefore express their requirement the same way
    in both modes and there is no ``if auth_enabled`` scattered through them —
    the mode is decided in one place, here.
    """

    __slots__ = ("username", "role", "authenticated")

    def __init__(self, username: str, role: str, authenticated: bool):
        self.username = username
        self.role = role
        self.authenticated = authenticated

    @classmethod
    def anonymous(cls) -> "Principal":
        from app.security import OPERATOR

        return cls(username="anonymous", role=OPERATOR, authenticated=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Principal({self.username!r}, role={self.role!r}, auth={self.authenticated})"


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def current_principal(authorization: str = Header(None)) -> Principal:
    """Resolve the caller, or 401.

    Open mode short-circuits to an anonymous operator. A token that *is*
    presented is still validated even in open mode, so a client cannot get a
    weaker check by sending a bad token than by sending none — and so the
    authenticated path is exercised by ordinary traffic.
    """
    from app.security import decode_access_token

    token = _bearer_token(authorization)

    if token is None:
        if settings.auth_required:
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Principal.anonymous()

    claims = decode_access_token(token)
    if claims is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Principal(username=claims["sub"], role=claims["role"], authenticated=True)


def require_role(required: str):
    """Dependency factory: caller must hold ``required`` or higher."""
    from app.security import role_satisfies

    async def _dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not role_satisfies(principal.role, required):
            raise HTTPException(
                status_code=403,
                detail=f"Requires the {required} role",
            )
        return principal

    return _dependency
