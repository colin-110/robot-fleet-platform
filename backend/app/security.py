"""Password hashing, JWT issuance, and role definitions.

Why authentication is a *mode*
------------------------------
``AUTH_MODE`` is ``open`` by default and ``required`` in production. That is a
deliberate split, not a shortcut:

* The hosted demo has to be clickable. A login wall on a portfolio deployment
  means the thing is never seen, and the data behind it is synthetic anyway.
* Any real deployment carries actual fleet positions, so ``APP_ENV=production``
  flips the default to ``required`` unless explicitly overridden.

The difference is one setting, and every route that needs identity asks for it
the same way in both modes — ``open`` simply resolves to an anonymous operator
rather than skipping the dependency. Keeping one code path means the
authenticated path is exercised by the demo's own traffic shape, instead of
being a branch that only runs in an environment nobody tests.

Token design
------------
HS256, signed with ``JWT_SECRET`` if set and otherwise derived from
``TELEMETRY_API_KEY`` — same reasoning as ``app/tickets.py``: no second secret
to configure, and rotating the master key invalidates outstanding tokens.

Claims are the registered ones plus ``role``. Tokens are short-lived and there
is no refresh token: this is a dashboard where a re-login is cheap, and a
refresh flow adds a revocation problem that a one-hour access token does not
have.
"""

import hmac
import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import bcrypt
import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ALGORITHM = "HS256"

# Ordered least- to most-privileged. A role satisfies any requirement at or
# below its own rank, so checks are `rank >= required` rather than a set of
# explicit grants that drifts as routes are added.
VIEWER = "viewer"
OPERATOR = "operator"
ADMIN = "admin"
ROLES: tuple[str, ...] = (VIEWER, OPERATOR, ADMIN)
_RANK = {role: index for index, role in enumerate(ROLES)}


def role_satisfies(actual: str, required: str) -> bool:
    """Does ``actual`` meet or exceed ``required``?"""
    if actual not in _RANK or required not in _RANK:
        return False
    return _RANK[actual] >= _RANK[required]


# ── Passwords ───────────────────────────────────────────────────────


def hash_password(plaintext: str) -> str:
    """bcrypt hash, cost from the library default."""
    # bcrypt silently truncates at 72 bytes, which would make two long
    # passwords sharing a prefix interchangeable. Reject rather than truncate.
    encoded = plaintext.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must be 72 bytes or fewer")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, password_hash: str) -> bool:
    """Constant-time verification. False on any malformed stored hash."""
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # A corrupt or empty hash must read as "wrong password", never as an
        # exception that a caller might mistake for a server fault and retry.
        return False


# ── Tokens ──────────────────────────────────────────────────────────


def _signing_key() -> str:
    return (
        settings.jwt_secret
        or sha256(settings.telemetry_api_key.encode("utf-8") + b"jwt").hexdigest()
    )


def issue_access_token(username: str, role: str, ttl_seconds: int | None = None) -> tuple[str, int]:
    """Sign an access token. Returns ``(token, expires_at_unix)``."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.jwt_ttl_seconds
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)
    payload = {
        "sub": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)
    return token, int(expires_at.timestamp())


def decode_access_token(token: str) -> dict | None:
    """Verify and decode. ``None`` for anything not currently valid.

    ``algorithms`` is pinned to a single value on purpose: accepting whatever
    the token's own header advertises is how ``alg: none`` and RS256-to-HS256
    confusion attacks work.
    """
    try:
        claims = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None

    if not claims.get("sub") or claims.get("role") not in _RANK:
        # A structurally valid token carrying a role we retired should not be
        # treated as if it were the lowest privilege — reject it.
        return None
    return claims


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
