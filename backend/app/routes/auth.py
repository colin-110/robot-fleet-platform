"""
API v1 routes — sign-in, identity, and short-lived console tickets.

Two layers, deliberately separate:

* **JWT** answers *who you are*. Only meaningful when ``AUTH_MODE=required``.
* **Console tickets** answer *may this browser tab open a socket*. They exist
  in both modes because a WebSocket handshake cannot carry an ``Authorization``
  header, so the credential has to ride in the URL — and a five-minute scoped
  ticket is a very different thing to leak into a proxy log than either the
  master API key or a one-hour identity token.

See ``app/security.py`` for why authentication is a mode, and ``app/tickets.py``
for the ticket format.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, current_principal, verify_read_access
from app.config import get_settings
from app.database import get_db
from app.repositories.user_repo import UserRepository
from app.schemas import (
    AuthConfigResponse,
    LoginRequest,
    PrincipalResponse,
    TicketResponse,
    TokenResponse,
)
from app.security import hash_password, issue_access_token, verify_password
from app.tickets import SCOPE_CONSOLE, issue_ticket

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1", tags=["auth"])

# Cost of one bcrypt verification, burned when the username does not exist so
# that "no such user" and "wrong password" take the same time. Without it,
# response timing enumerates valid usernames.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


@router.get("/auth/config", response_model=AuthConfigResponse)
async def auth_config():
    """Whether this deployment expects a login. Public by necessity — the
    dashboard has to ask before it can know what to render."""
    return AuthConfigResponse(
        auth_mode=settings.resolved_auth_mode,
        login_required=settings.auth_required,
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(credentials: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a username and password for a short-lived access token."""
    if not settings.auth_required:
        # Issuing identity tokens from a deployment with no accounts would be
        # theatre: anyone could ask and anyone would receive.
        raise HTTPException(
            status_code=404,
            detail="This deployment runs with AUTH_MODE=open; there is nothing to sign in to.",
        )

    repo = UserRepository(db)
    user = await repo.get_by_username(credentials.username)

    # One failure message and one timing profile for every rejection reason:
    # "unknown user" and "wrong password" must be indistinguishable.
    if user is None:
        verify_password(credentials.password, _DUMMY_HASH)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token, expires_at = issue_access_token(user.username, user.role)

    # Best-effort: a failure to stamp last_login is not a reason to deny a
    # sign-in that has already succeeded.
    try:
        await repo.touch_last_login(user.id)
    except Exception:
        logger.warning("Could not record last_login for %s", user.username, exc_info=True)

    logger.info("Login succeeded for %s (role=%s)", user.username, user.role)
    return TokenResponse(
        access_token=token,
        expires_at=expires_at,
        username=user.username,
        role=user.role,
    )


@router.get("/auth/me", response_model=PrincipalResponse)
async def whoami(principal: Principal = Depends(current_principal)):
    """The caller's identity. In open mode this reports the anonymous
    operator rather than 401, which is what lets the dashboard render the same
    way against either configuration."""
    return PrincipalResponse(
        username=principal.username,
        role=principal.role,
        authenticated=principal.authenticated,
    )


@router.post("/auth/ticket", response_model=TicketResponse)
async def create_console_ticket(
    principal: Principal = Depends(current_principal),
    _=Depends(verify_read_access),
):
    """Issue a short-lived, scoped ticket for the operator console.

    In ``required`` mode ``current_principal`` has already rejected anyone
    without a valid token, so a ticket cannot be minted anonymously. In
    ``open`` mode it is public, matching the read endpoints beside it.

    A ticket authorizes the live feed and command dispatch — never telemetry
    ingest, which still requires the master key.
    """
    ticket, expires_at = issue_ticket(SCOPE_CONSOLE)
    return TicketResponse(ticket=ticket, expires_at=expires_at, scope=SCOPE_CONSOLE)


async def bootstrap_admin_user() -> None:
    """Create the initial admin account if the users table is empty.

    Runs at startup. Without it a fresh deployment in ``required`` mode has no
    way in — the first account has to come from somewhere, and a migration that
    hardcodes a password is worse than an env var the operator sets once.

    Only ever creates the *first* account, so restarting the app cannot
    resurrect a deliberately deleted admin or silently reset its password.
    """
    if not settings.auth_required:
        return
    if not (settings.bootstrap_admin_username and settings.bootstrap_admin_password):
        logger.warning(
            "AUTH_MODE=required but no BOOTSTRAP_ADMIN_USERNAME/PASSWORD set. "
            "If the users table is empty, nobody can sign in."
        )
        return

    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            repo = UserRepository(session)
            if await repo.count() > 0:
                return
            await repo.create(
                username=settings.bootstrap_admin_username,
                password_hash=hash_password(settings.bootstrap_admin_password),
                role="admin",
            )
            logger.info(
                "Created bootstrap admin %r. Change this password.",
                settings.bootstrap_admin_username,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        # A failed bootstrap must not stop the app from booting; the operator
        # can create the account by hand, and the log says what happened.
        logger.exception("Bootstrap admin creation failed")
