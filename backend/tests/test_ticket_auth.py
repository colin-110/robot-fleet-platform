"""End-to-end checks on ticket-based authentication.

``conftest.py`` overrides the auth dependencies for every other suite so route
behaviour can be tested without carrying credentials. These tests deliberately
lift that override, because the point here *is* the credential check.
"""

import pytest
from starlette.websockets import WebSocketDisconnect

from app.auth import verify_console_access
from app.config import get_settings
from app.main import app, websocket_endpoint
from app.tickets import SCOPE_CONSOLE, issue_ticket
from app.websocket_manager import manager

settings = get_settings()


@pytest.fixture
def real_console_auth():
    """Restore the genuine dependency for the duration of one test."""
    override = app.dependency_overrides.pop(verify_console_access, None)
    yield
    if override is not None:
        app.dependency_overrides[verify_console_access] = override


@pytest.mark.asyncio
async def test_ticket_endpoint_issues_a_usable_ticket(client):
    response = await client.post("/api/v1/auth/ticket")

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == SCOPE_CONSOLE
    assert body["ticket"].startswith("v1.console.")
    assert body["expires_at"] > 0


@pytest.mark.asyncio
async def test_issued_ticket_authorizes_command_dispatch(client, real_console_auth):
    ticket = (await client.post("/api/v1/auth/ticket")).json()["ticket"]

    response = await client.post(
        "/api/v1/commands/1",
        json={"command_type": "RETURN_TO_BASE"},
        headers={"X-Console-Ticket": ticket},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_command_dispatch_rejects_a_missing_credential(client, real_console_auth):
    response = await client.post("/api/v1/commands/1", json={"command_type": "RETURN_TO_BASE"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_command_dispatch_rejects_a_forged_ticket(client, real_console_auth):
    response = await client.post(
        "/api/v1/commands/1",
        json={"command_type": "RETURN_TO_BASE"},
        headers={"X-Console-Ticket": "v1.console.9999999999.nonce.deadbeef"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_master_key_still_authorizes_command_dispatch(client, real_console_auth):
    """Server-side callers legitimately hold the key and must keep working."""
    response = await client.post(
        "/api/v1/commands/1",
        json={"command_type": "RETURN_TO_BASE"},
        headers={"X-API-Key": settings.telemetry_api_key},
    )

    assert response.status_code == 200


# ── WebSocket handshake ───────────────────────────────────────
#
# Driven against the endpoint coroutine rather than through TestClient: its
# synchronous WebSocket path trips a Pydantic 2.11 deprecation inside the
# installed FastAPI, which has nothing to do with the credential check under
# test here.


class HandshakeSocket:
    """A WebSocket that disconnects immediately once accepted."""

    def __init__(self):
        self.accepted = False
        self.closed_with = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=None, reason=None):
        self.closed_with = (code, reason)

    async def receive_text(self):
        raise WebSocketDisconnect()

    async def send_json(self, data):
        pass

    async def send_text(self, text):
        pass


async def _handshake(**credentials) -> HandshakeSocket:
    socket = HandshakeSocket()
    await websocket_endpoint(socket, **{"api_key": None, "ticket": None, **credentials})
    manager.disconnect(socket)
    return socket


@pytest.mark.asyncio
async def test_websocket_accepts_a_valid_ticket():
    ticket, _ = issue_ticket(SCOPE_CONSOLE)

    socket = await _handshake(ticket=ticket)

    assert socket.accepted is True
    assert socket.closed_with is None


@pytest.mark.asyncio
async def test_websocket_accepts_the_master_key():
    """The load harnesses connect this way and must keep working."""
    socket = await _handshake(api_key=settings.telemetry_api_key)

    assert socket.accepted is True


@pytest.mark.asyncio
async def test_websocket_rejects_a_missing_credential():
    socket = await _handshake()

    assert socket.accepted is False
    assert socket.closed_with[0] == 4001


@pytest.mark.asyncio
async def test_websocket_rejects_an_expired_ticket():
    expired, _ = issue_ticket(SCOPE_CONSOLE, ttl_seconds=-1)

    socket = await _handshake(ticket=expired)

    assert socket.accepted is False
    assert socket.closed_with[0] == 4001


@pytest.mark.asyncio
async def test_websocket_rejects_a_forged_ticket():
    socket = await _handshake(ticket="v1.console.9999999999.nonce.deadbeef")

    assert socket.accepted is False
    assert socket.closed_with[0] == 4001
