"""Tests for short-lived console tickets.

The property under test is that a ticket cannot be forged, widened, or extended
by whoever holds it — the whole point of moving the browser off the master API
key is defeated if any of those are possible.
"""

import time
from unittest.mock import patch

import pytest

from app.tickets import SCOPE_CONSOLE, issue_ticket, verify_ticket


def test_issued_ticket_verifies():
    ticket, expires_at = issue_ticket(SCOPE_CONSOLE)

    assert verify_ticket(ticket, SCOPE_CONSOLE) is True
    assert expires_at > time.time()


def test_ticket_carries_version_and_scope():
    ticket, _ = issue_ticket(SCOPE_CONSOLE)
    version, scope, _expiry, _nonce, _sig = ticket.split(".")

    assert version == "v1"
    assert scope == SCOPE_CONSOLE


def test_two_tickets_issued_together_are_distinct():
    """The nonce makes a repeat in a log a replay rather than a coincidence."""
    first, _ = issue_ticket(SCOPE_CONSOLE)
    second, _ = issue_ticket(SCOPE_CONSOLE)

    assert first != second


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "",
        "garbage",
        "v1.console.9999999999",  # too few fields
        "v1.console.9999999999.nonce.deadbeef.extra",  # too many
    ],
)
def test_malformed_tickets_are_rejected(candidate):
    assert verify_ticket(candidate, SCOPE_CONSOLE) is False


def test_expired_ticket_is_rejected():
    ticket, _ = issue_ticket(SCOPE_CONSOLE, ttl_seconds=-1)

    assert verify_ticket(ticket, SCOPE_CONSOLE) is False


def test_tampered_signature_is_rejected():
    ticket, _ = issue_ticket(SCOPE_CONSOLE)
    version, scope, expiry, nonce, signature = ticket.split(".")
    flipped = "f" if signature[0] != "f" else "0"

    forged = ".".join((version, scope, expiry, nonce, flipped + signature[1:]))

    assert verify_ticket(forged, SCOPE_CONSOLE) is False


def test_holder_cannot_extend_their_own_expiry():
    """The expiry is inside the signed payload, so editing it breaks the HMAC."""
    ticket, _ = issue_ticket(SCOPE_CONSOLE, ttl_seconds=1)
    version, scope, _expiry, nonce, signature = ticket.split(".")
    far_future = str(int(time.time()) + 86_400)

    extended = ".".join((version, scope, far_future, nonce, signature))

    assert verify_ticket(extended, SCOPE_CONSOLE) is False


def test_holder_cannot_widen_their_own_scope():
    ticket, _ = issue_ticket(SCOPE_CONSOLE)
    version, _scope, expiry, nonce, signature = ticket.split(".")

    widened = ".".join((version, "ingest", expiry, nonce, signature))

    assert verify_ticket(widened, "ingest") is False


def test_ticket_for_one_scope_does_not_satisfy_another():
    ticket, _ = issue_ticket(SCOPE_CONSOLE)

    assert verify_ticket(ticket, "ingest") is False


def test_unknown_version_is_rejected():
    """Leaves room to rotate the format without honouring the old one."""
    ticket, _ = issue_ticket(SCOPE_CONSOLE)
    _version, scope, expiry, nonce, signature = ticket.split(".")

    assert verify_ticket(".".join(("v2", scope, expiry, nonce, signature)), SCOPE_CONSOLE) is False


def test_non_numeric_expiry_is_rejected():
    """Signature is checked first, so this only matters for a valid-looking
    payload — but a crash here would be a denial of service on the handshake."""
    assert verify_ticket("v1.console.not-a-number.nonce.deadbeef", SCOPE_CONSOLE) is False


def test_rotating_the_api_key_invalidates_outstanding_tickets():
    """The signing key is derived from the master key, so a rotation revokes
    every ticket already in the wild — the behaviour you want from a rotation."""
    from app import tickets

    ticket, _ = issue_ticket(SCOPE_CONSOLE)
    assert verify_ticket(ticket, SCOPE_CONSOLE) is True

    rotated = tickets.settings.model_copy(update={"telemetry_api_key": "a-different-key"})
    with patch.object(tickets, "settings", rotated):
        assert verify_ticket(ticket, SCOPE_CONSOLE) is False
