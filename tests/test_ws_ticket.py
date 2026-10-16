"""WS ticket handshake integration tests (audit 2026-10-16, B2).

Covers the full path: HTTP ticket mint (X-API-Key header) -> one-time
consumption on the WS upgrade. Also locks the B2 regression: a raw
``api_key=`` query param on the WS URL is no longer an auth path.
"""

import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.config.settings import settings
from backend.main import app
from backend.ws import ticket as ws_ticket

_KEY = "unit-test-key-123"


def _issue_ticket(client: TestClient) -> str:
    r = client.get("/api/v1/auth/ws-ticket", headers={"X-API-Key": _KEY})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


class TestWsTicketEndpoint(unittest.TestCase):
    def setUp(self):
        ws_ticket.ticket_store.reset()

    def test_requires_key_when_auth_enabled(self):
        with patch.object(settings, "API_KEY", _KEY):
            client = TestClient(app)
            r = client.get("/api/v1/auth/ws-ticket")
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json()["error"]["code"], "unauthorized")

    def test_wrong_key_rejected(self):
        with patch.object(settings, "API_KEY", _KEY):
            client = TestClient(app)
            r = client.get("/api/v1/auth/ws-ticket", headers={"X-API-Key": "nope"})
            self.assertEqual(r.status_code, 401)

    def test_issues_ticket_with_key(self):
        with patch.object(settings, "API_KEY", _KEY):
            client = TestClient(app)
            r = client.get("/api/v1/auth/ws-ticket", headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsInstance(body["ticket"], str)
        self.assertGreaterEqual(len(body["ticket"]), 20)
        self.assertEqual(body["ttl"], ws_ticket.TICKET_TTL)

    def test_no_key_configured_issues_without_auth(self):
        with patch.object(settings, "API_KEY", ""):
            client = TestClient(app)
            r = client.get("/api/v1/auth/ws-ticket")
            self.assertEqual(r.status_code, 200)
            self.assertIn("ticket", r.json())


class TestWsHandshakeTicket(unittest.TestCase):
    def setUp(self):
        ws_ticket.ticket_store.reset()

    def test_fresh_ticket_accepted(self):
        with patch.object(settings, "API_KEY", _KEY):
            client = TestClient(app)
            t = _issue_ticket(client)
            with client.websocket_connect(f"/ws/hardware?ticket={t}") as ws:
                ws.close()

    def test_ticket_is_single_use(self):
        with patch.object(settings, "API_KEY", _KEY):
            client = TestClient(app)
            t = _issue_ticket(client)
            with client.websocket_connect(f"/ws/hardware?ticket={t}"):
                pass
            with self.assertRaises(WebSocketDisconnect), client.websocket_connect(f"/ws/hardware?ticket={t}"):
                pass

    def test_raw_api_key_no_longer_accepted(self):
        # B2 regression lock: key-in-URL must stop working.
        with patch.object(settings, "API_KEY", _KEY):
            client = TestClient(app)
            with self.assertRaises(WebSocketDisconnect), client.websocket_connect(f"/ws/hardware?api_key={_KEY}"):
                pass

    def test_invalid_ticket_rejected(self):
        with patch.object(settings, "API_KEY", _KEY):
            client = TestClient(app)
            with self.assertRaises(WebSocketDisconnect), client.websocket_connect("/ws/hardware?ticket=bogus"):
                pass

    def test_expired_ticket_rejected(self):
        with patch.object(settings, "API_KEY", _KEY):
            client = TestClient(app)
            with patch.object(ws_ticket, "TICKET_TTL", 0.05):
                t = _issue_ticket(client)
            time.sleep(0.1)
            with self.assertRaises(WebSocketDisconnect), client.websocket_connect(f"/ws/hardware?ticket={t}"):
                pass

    def test_no_key_configured_no_ticket_needed(self):
        with patch.object(settings, "API_KEY", ""):
            client = TestClient(app)
            with client.websocket_connect("/ws/hardware") as ws:
                ws.close()


if __name__ == "__main__":
    unittest.main()
