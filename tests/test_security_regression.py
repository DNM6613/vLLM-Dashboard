"""Security regression tests (audit 2026-10-16: B1 / B2 / B3 locking surface).

(a) install-status log_file path validation — the endpoint must reject
    non-whitelisted paths with a 4xx (same discipline as /download/status).
    Fixed 2026-10-16 (API-layer whitelist); the former xfail marker removed.
(b) middleware auth: _auth_enabled without/with API_KEY, unauthorized
    requests get a 401 envelope, authenticated/OPTIONS pass through.
(c) /docs and /openapi.json must be inside the auth gate — xfail(strict)
    until B1 is addressed (kept as-is per the local/LAN threat model).
(d) WS auth is a one-time short-lived ticket (B2, fixed 2026-10-16): the
    raw API key in the WS URL is no longer an auth path.
"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.responses import JSONResponse

from backend.config.settings import settings
from backend.middleware import (
    _auth_enabled,
    _requires_auth,
    auth_middleware,
    ws_api_key_valid,
)
from backend.ws import ticket as ws_ticket

_KEY = "unit-test-key-123"


def _request(path="/api/v1/models", method="GET", headers=None):
    state = SimpleNamespace(request_id="sec-req-1")
    return SimpleNamespace(
        url=SimpleNamespace(path=path), method=method,
        headers=headers or {}, state=state, scope={"type": "http"},
    )


async def _call_next(request):
    return "PASSED_THROUGH"


class TestAuthEnabled(unittest.TestCase):
    def test_no_key_means_auth_disabled(self):
        with patch.object(settings, "API_KEY", ""):
            self.assertFalse(_auth_enabled())

    def test_key_set_means_auth_enabled(self):
        with patch.object(settings, "API_KEY", _KEY):
            self.assertTrue(_auth_enabled())

    def test_api_scope_requires_auth(self):
        self.assertTrue(_requires_auth("/api/v1/models"))
        self.assertTrue(_requires_auth("/ws/console"))
        self.assertTrue(_requires_auth("/health"))
        self.assertTrue(_requires_auth("/metrics"))


class TestAuthMiddleware(unittest.TestCase):
    def test_missing_key_returns_401_envelope(self):
        with patch.object(settings, "API_KEY", _KEY):
            resp = asyncio.run(auth_middleware(_request(), _call_next))
            self.assertIsInstance(resp, JSONResponse)
            self.assertEqual(resp.status_code, 401)
            body = json.loads(resp.body)
            self.assertEqual(body["error"]["code"], "unauthorized")
            self.assertIn("x-request-id", resp.headers)

    def test_wrong_key_returns_401(self):
        with patch.object(settings, "API_KEY", _KEY):
            req = _request(headers={"X-API-Key": "wrong-key"})
            resp = asyncio.run(auth_middleware(req, _call_next))
            self.assertEqual(resp.status_code, 401)

    def test_correct_key_passes_through(self):
        with patch.object(settings, "API_KEY", _KEY):
            req = _request(headers={"X-API-Key": _KEY})
            result = asyncio.run(auth_middleware(req, _call_next))
            self.assertEqual(result, "PASSED_THROUGH")

    def test_options_preflight_passes_without_key(self):
        with patch.object(settings, "API_KEY", _KEY):
            req = _request(method="OPTIONS")
            result = asyncio.run(auth_middleware(req, _call_next))
            self.assertEqual(result, "PASSED_THROUGH")

    def test_unprotected_path_passes_without_key_header(self):
        with patch.object(settings, "API_KEY", _KEY):
            req = _request(path="/favicon.ico")
            result = asyncio.run(auth_middleware(req, _call_next))
            self.assertEqual(result, "PASSED_THROUGH")

    def test_auth_disabled_passes_everything(self):
        with patch.object(settings, "API_KEY", ""):
            req = _request()
            result = asyncio.run(auth_middleware(req, _call_next))
            self.assertEqual(result, "PASSED_THROUGH")


class TestWsTicket(unittest.TestCase):
    """B2: WS auth is a one-time ticket; the key no longer travels in the URL."""

    def setUp(self):
        ws_ticket.ticket_store.reset()

    def test_no_key_configured_allows_ws(self):
        with patch.object(settings, "API_KEY", ""):
            ws = SimpleNamespace(query_params={})
            self.assertTrue(ws_api_key_valid(ws))

    def test_raw_api_key_param_rejected(self):
        # B2 regression lock: key-in-URL is no longer an auth path.
        with patch.object(settings, "API_KEY", _KEY):
            ws = SimpleNamespace(query_params={"api_key": _KEY})
            self.assertFalse(ws_api_key_valid(ws))

    def test_fresh_ticket_accepted_once(self):
        with patch.object(settings, "API_KEY", _KEY):
            t = ws_ticket.ticket_store.issue()
            ws = SimpleNamespace(query_params={"ticket": t})
            self.assertTrue(ws_api_key_valid(ws))
            self.assertFalse(ws_api_key_valid(ws))  # single-use

    def test_invalid_ticket_rejected(self):
        with patch.object(settings, "API_KEY", _KEY):
            ws = SimpleNamespace(query_params={"ticket": "bogus"})
            self.assertFalse(ws_api_key_valid(ws))


class TestInstallStatusPathValidation(unittest.TestCase):
    """B3: install-status must whitelist log_file like /download/status does."""

    def test_download_status_whitelist_rejects_traversal(self):
        from fastapi.testclient import TestClient

        from backend.main import app
        client = TestClient(app)
        r = client.get("/api/v1/models/download/status",
                       params={"log_file": "/etc/passwd"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    def test_install_status_rejects_non_whitelisted_path(self):
        from fastapi.testclient import TestClient

        from backend.main import app
        client = TestClient(app)
        r = client.get("/api/v1/models/install-status",
                       params={"tool": "hf", "log_file": "/etc/passwd"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    def test_install_status_rejects_unknown_tool(self):
        from fastapi.testclient import TestClient

        from backend.main import app
        client = TestClient(app)
        r = client.get("/api/v1/models/install-status",
                       params={"tool": "git", "log_file": "/tmp/cli_install_hf.log"})
        self.assertEqual(r.status_code, 400)

    def test_install_status_allows_whitelisted_path(self):
        from unittest.mock import MagicMock, patch

        from fastapi.testclient import TestClient

        from backend.main import app
        from backend.service import model_service
        executor = MagicMock()
        executor.get_install_status.return_value = {"status": "running", "message": "installing"}
        with patch.object(model_service, "get_remote_executor", return_value=executor):
            client = TestClient(app)
            r = client.get("/api/v1/models/install-status",
                           params={"tool": "hf", "log_file": "/tmp/cli_install_hf.log"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "running")
        executor.get_install_status.assert_called_once_with("hf", "/tmp/cli_install_hf.log")


class TestDocsAuthGate(unittest.TestCase):
    """B1: docs/openapi must not leak the API attack surface map."""

    @pytest.mark.xfail(
        reason="待 B1 修复后移除标记：/docs 与 /openapi.json 尚未纳入鉴权名单",
        strict=True,
    )
    def test_docs_requires_auth_when_key_set(self):
        with patch.object(settings, "API_KEY", _KEY):
            self.assertTrue(_requires_auth("/docs"))

    @pytest.mark.xfail(
        reason="待 B1 修复后移除标记：/docs 与 /openapi.json 尚未纳入鉴权名单",
        strict=True,
    )
    def test_openapi_json_requires_auth_when_key_set(self):
        with patch.object(settings, "API_KEY", _KEY):
            self.assertTrue(_requires_auth("/openapi.json"))


if __name__ == "__main__":
    unittest.main()
