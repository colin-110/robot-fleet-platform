"""JWT authentication, roles, and the open/required mode split.

The suite runs in ``open`` mode (see conftest), so every test that needs
identity enforcement flips ``auth_mode`` explicitly. That mirrors the real
deployment split rather than testing only one half of it.
"""

import time

import jwt
import pytest

from app.auth import Principal, require_role
from app.config import get_settings
from app.repositories.user_repo import UserRepository
from app.security import (
    ADMIN,
    OPERATOR,
    VIEWER,
    _signing_key,
    decode_access_token,
    hash_password,
    issue_access_token,
    role_satisfies,
    verify_password,
)

settings = get_settings()


@pytest.fixture
def auth_required(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "required")


async def _make_user(db, username: str, password: str, role: str):
    return await UserRepository(db).create(username, hash_password(password), role)


# ── Password hashing ────────────────────────────────────────────────


def test_password_round_trips():
    stored = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", stored) is True
    assert verify_password("wrong password", stored) is False


def test_hash_is_salted():
    """Two identical passwords must not produce identical hashes."""
    assert hash_password("same") != hash_password("same")


def test_overlong_password_is_rejected_not_truncated():
    """bcrypt silently truncates at 72 bytes, which would make two long
    passwords sharing a prefix interchangeable."""
    with pytest.raises(ValueError):
        hash_password("x" * 73)


def test_corrupt_stored_hash_reads_as_wrong_password():
    """Never an exception a caller might mistake for a transient fault."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


# ── Roles ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("actual", "required", "expected"),
    [
        (VIEWER, VIEWER, True),
        (VIEWER, OPERATOR, False),
        (VIEWER, ADMIN, False),
        (OPERATOR, VIEWER, True),
        (OPERATOR, OPERATOR, True),
        (OPERATOR, ADMIN, False),
        (ADMIN, VIEWER, True),
        (ADMIN, OPERATOR, True),
        (ADMIN, ADMIN, True),
    ],
)
def test_role_ranking(actual, required, expected):
    assert role_satisfies(actual, required) is expected


def test_unknown_roles_never_satisfy_anything():
    assert role_satisfies("superuser", VIEWER) is False
    assert role_satisfies(VIEWER, "wizard") is False


# ── Tokens ──────────────────────────────────────────────────────────


def test_issued_token_decodes_with_its_claims():
    token, expires_at = issue_access_token("alice", OPERATOR)
    claims = decode_access_token(token)

    assert claims["sub"] == "alice"
    assert claims["role"] == OPERATOR
    assert claims["exp"] == expires_at


def test_expired_token_is_rejected():
    token, _ = issue_access_token("alice", OPERATOR, ttl_seconds=-1)

    assert decode_access_token(token) is None


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode(
        {"sub": "mallory", "role": ADMIN, "exp": int(time.time()) + 3600},
        "a-different-signing-key-long-enough-for-sha256-hmac",
        algorithm="HS256",
    )

    assert decode_access_token(forged) is None


def test_alg_none_token_is_rejected():
    """The classic JWT bypass: an unsigned token claiming no algorithm."""
    unsigned = jwt.encode({"sub": "mallory", "role": ADMIN}, key="", algorithm="none")

    assert decode_access_token(unsigned) is None


def test_token_carrying_an_unknown_role_is_rejected():
    """A retired role must not silently degrade to the lowest privilege."""
    token = jwt.encode(
        {"sub": "alice", "role": "superuser", "exp": int(time.time()) + 3600},
        _signing_key(),
        algorithm="HS256",
    )

    assert decode_access_token(token) is None


def test_rotating_the_api_key_invalidates_tokens(monkeypatch):
    token, _ = issue_access_token("alice", OPERATOR)
    assert decode_access_token(token) is not None

    monkeypatch.setattr(settings, "telemetry_api_key", "a-completely-different-key")
    assert decode_access_token(token) is None


# ── The mode split ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_endpoint_reports_open_mode(client):
    response = await client.get("/api/v1/auth/config")

    assert response.status_code == 200
    assert response.json() == {"auth_mode": "open", "login_required": False}


@pytest.mark.asyncio
async def test_config_endpoint_reports_required_mode(client, auth_required):
    response = await client.get("/api/v1/auth/config")

    assert response.json() == {"auth_mode": "required", "login_required": True}


@pytest.mark.asyncio
async def test_login_is_absent_in_open_mode(client):
    """Handing out identity tokens where there are no accounts is theatre."""
    response = await client.post(
        "/api/v1/auth/login", json={"username": "anyone", "password": "anything"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_open_mode_resolves_an_anonymous_operator(client):
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "username": "anonymous",
        "role": OPERATOR,
        "authenticated": False,
    }


@pytest.mark.asyncio
async def test_required_mode_rejects_an_unauthenticated_caller(client, auth_required):
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


# ── Login ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_returns_a_usable_token(client, db, auth_required):
    await _make_user(db, "alice", "s3cret-password", OPERATOR)

    response = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "s3cret-password"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["username"] == "alice"
    assert body["role"] == OPERATOR
    assert decode_access_token(body["access_token"])["sub"] == "alice"


@pytest.mark.asyncio
async def test_login_rejects_a_wrong_password(client, db, auth_required):
    await _make_user(db, "alice", "s3cret-password", OPERATOR)

    response = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "guess"}
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_user_is_indistinguishable_from_a_wrong_password(client, db, auth_required):
    """Different messages here would enumerate valid usernames."""
    await _make_user(db, "alice", "s3cret-password", OPERATOR)

    wrong_password = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "guess"}
    )
    no_such_user = await client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "guess"}
    )

    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json()["detail"] == no_such_user.json()["detail"]


@pytest.mark.asyncio
async def test_disabled_account_cannot_sign_in(client, db, auth_required):
    user = await _make_user(db, "bob", "s3cret-password", OPERATOR)
    user.is_active = False
    await db.commit()

    response = await client.post(
        "/api/v1/auth/login", json={"username": "bob", "password": "s3cret-password"}
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_signed_in_caller_is_reported_by_me(client, db, auth_required):
    await _make_user(db, "alice", "s3cret-password", ADMIN)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "s3cret-password"}
    )
    token = login.json()["access_token"]

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.json() == {"username": "alice", "role": ADMIN, "authenticated": True}


@pytest.mark.parametrize("header", ["", "Bearer", "Basic abc123", "Bearer   ", "token abc"])
@pytest.mark.asyncio
async def test_malformed_authorization_headers_are_refused(client, auth_required, header):
    response = await client.get("/api/v1/auth/me", headers={"Authorization": header})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_bad_token_is_refused_even_in_open_mode(client):
    """Sending a bad token must not be weaker than sending none at all."""
    response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})

    assert response.status_code == 401


# ── Role enforcement on dispatch ────────────────────────────────────


@pytest.mark.asyncio
async def test_viewer_cannot_dispatch_commands(client, db, auth_required):
    await _make_user(db, "vera", "s3cret-password", VIEWER)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "vera", "password": "s3cret-password"}
    )
    token = login.json()["access_token"]

    response = await client.post(
        "/api/v1/commands/1",
        json={"command_type": "RETURN_TO_BASE"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_operator_can_dispatch_commands(client, db, auth_required):
    await _make_user(db, "olive", "s3cret-password", OPERATOR)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "olive", "password": "s3cret-password"}
    )
    token = login.json()["access_token"]

    response = await client.post(
        "/api/v1/commands/1",
        json={"command_type": "RETURN_TO_BASE"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ticket_cannot_be_minted_anonymously_when_auth_is_required(client, auth_required):
    """Otherwise the login wall is bypassable in one request."""
    response = await client.post("/api/v1/auth/ticket")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_ticket_is_public_in_open_mode(client):
    response = await client.post("/api/v1/auth/ticket")

    assert response.status_code == 200
    assert response.json()["scope"] == "console"


# ── require_role directly ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_role_allows_a_higher_rank():
    dependency = require_role(OPERATOR)

    result = await dependency(Principal("admin-user", ADMIN, True))

    assert result.username == "admin-user"


@pytest.mark.asyncio
async def test_require_role_blocks_a_lower_rank():
    from fastapi import HTTPException

    dependency = require_role(ADMIN)

    with pytest.raises(HTTPException) as excinfo:
        await dependency(Principal("viewer-user", VIEWER, True))

    assert excinfo.value.status_code == 403


# ── Bootstrap admin ─────────────────────────────────────────────────


@pytest.fixture
def bootstrap_creds(monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_username", "rootadmin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "bootstrap-password")


@pytest.mark.asyncio
async def test_bootstrap_creates_the_first_admin(db, auth_required, bootstrap_creds):
    from app.routes.auth import bootstrap_admin_user

    await bootstrap_admin_user()

    user = await UserRepository(db).get_by_username("rootadmin")
    assert user is not None
    assert user.role == ADMIN
    assert verify_password("bootstrap-password", user.password_hash) is True


@pytest.mark.asyncio
async def test_bootstrap_does_nothing_in_open_mode(db, bootstrap_creds):
    """No accounts exist in open mode, so creating one would be surprising."""
    from app.routes.auth import bootstrap_admin_user

    await bootstrap_admin_user()

    assert await UserRepository(db).count() == 0


@pytest.mark.asyncio
async def test_bootstrap_never_touches_an_existing_account(db, auth_required, bootstrap_creds):
    """Restarting the app must not resurrect a deliberately deleted admin, or
    silently reset the password of an account someone already changed."""
    from app.routes.auth import bootstrap_admin_user

    await _make_user(db, "rootadmin", "the-real-password", ADMIN)

    await bootstrap_admin_user()

    user = await UserRepository(db).get_by_username("rootadmin")
    assert verify_password("the-real-password", user.password_hash) is True
    assert verify_password("bootstrap-password", user.password_hash) is False
    assert await UserRepository(db).count() == 1


@pytest.mark.asyncio
async def test_bootstrap_warns_when_required_mode_has_no_credentials(
    db, auth_required, monkeypatch, caplog
):
    """A required-mode deployment with an empty users table and no bootstrap
    credentials is unreachable — that has to be loud, not silent."""
    from app.routes.auth import bootstrap_admin_user

    monkeypatch.setattr(settings, "bootstrap_admin_username", None)
    monkeypatch.setattr(settings, "bootstrap_admin_password", None)

    with caplog.at_level("WARNING"):
        await bootstrap_admin_user()

    assert any("nobody can sign in" in record.getMessage() for record in caplog.records)
    assert await UserRepository(db).count() == 0
