"""
API v1 routes — short-lived console tickets for browser clients.

See ``app/tickets.py`` for why the dashboard is issued a ticket instead of
being compiled with the master API key.
"""

import logging

from fastapi import APIRouter, Depends

from app.auth import verify_read_access
from app.schemas import TicketResponse
from app.tickets import SCOPE_CONSOLE, issue_ticket

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/auth/ticket", response_model=TicketResponse)
async def create_console_ticket(_=Depends(verify_read_access)):
    """Issue a short-lived, scoped ticket for the operator console.

    Gated by ``REQUIRE_AUTH_FOR_READS``, the same switch as the other read
    endpoints: public on the hosted demo, key-protected wherever that setting
    is on. A ticket authorizes the live feed and command dispatch — never
    telemetry ingest, which still requires the master key.
    """
    ticket, expires_at = issue_ticket(SCOPE_CONSOLE)
    return TicketResponse(ticket=ticket, expires_at=expires_at, scope=SCOPE_CONSOLE)
