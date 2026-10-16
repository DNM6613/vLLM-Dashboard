"""WebSocket auth ticket endpoint (audit 2026-10-16, B2).

Mints a one-time short-lived ticket so the API key stays in the request
header and never appears in the WS URL / access logs. The endpoint itself
sits behind the normal X-API-Key gate (all /api/ routes).
"""

from fastapi import APIRouter, HTTPException

from ..ws import ticket as ws_ticket

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/ws-ticket")
def issue_ws_ticket():
    try:
        t = ws_ticket.ticket_store.issue()
    except ws_ticket.TicketStoreFull:
        raise HTTPException(status_code=503, detail="Ticket store saturated; retry shortly")
    return {"ticket": t, "ttl": ws_ticket.TICKET_TTL}
