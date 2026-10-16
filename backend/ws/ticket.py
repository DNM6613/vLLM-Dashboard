"""One-time, short-lived WebSocket auth tickets (audit 2026-10-16, B2).

The API key authenticates the HTTP call that mints a ticket (sent in the
X-API-Key header, never in a URL). The WebSocket upgrade then consumes the
ticket, so the key no longer appears in WS URLs or access logs. Tickets are
single-use, TTL-bounded, and held in a capped in-memory store — safe for the
single-process deployment (no shared state between workers).
"""

import secrets
import threading
import time

TICKET_TTL = 60.0
_MAX_TICKETS = 4096


class TicketStoreFull(RuntimeError):
    """Raised when the store is saturated; caller should retry shortly."""


class TicketStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tickets: dict[str, float] = {}

    def _prune_expired(self) -> None:
        now = time.monotonic()
        for t in [t for t, exp in self._tickets.items() if exp <= now]:
            del self._tickets[t]

    def issue(self) -> str:
        with self._lock:
            self._prune_expired()
            if len(self._tickets) >= _MAX_TICKETS:
                raise TicketStoreFull("ws ticket store saturated")
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = time.monotonic() + TICKET_TTL
            return ticket

    def consume(self, ticket: str) -> bool:
        """Consume a ticket; True only if it exists, is unexpired, and unused."""
        with self._lock:
            expires = self._tickets.pop(ticket, None)
            return expires is not None and expires > time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._tickets.clear()


ticket_store = TicketStore()
