"""v6 /config/server/health: always 200 with structured error_code.

The API light renders the specific error (refused / timeout / http:<code> /
no_response) instead of a generic 503. No real network: the TCP probe and
the httpx client are patched.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from backend.config import connection_probes as cp_mod
from backend.config import remote_client
from backend.config.remote_client import config_manager
from backend.config.server_config import ServerConfig

HOST = "10.99.99.99"


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Resp:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class _Client:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def get(self, url, timeout=None, headers=None):
        if self._exc is not None:
            raise self._exc
        return self._resp


def _target():
    return SimpleNamespace(base_url=f"http://{HOST}:8000", port=8000,
                           headers={})


class TestGetHealth(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = config_manager.config
        config_manager.config = ServerConfig(host=HOST)

    def tearDown(self):
        config_manager.config = self._saved

    async def _run(self, tcp_exc, client):
        def fake_conn(addr, timeout=None):
            if tcp_exc is not None:
                raise tcp_exc
            return _FakeConn()

        with patch.object(cp_mod.socket, "create_connection",
                          side_effect=fake_conn), \
             patch.object(remote_client, "resolve_api_target",
                          return_value=_target()), \
             patch.object(remote_client, "get_http_client",
                          return_value=client):
            return await config_manager.get_health()

    async def test_refused(self):
        out = await self._run(ConnectionRefusedError(), _Client())
        self.assertEqual(out["reachable"], False)
        self.assertEqual(out["service_ok"], False)
        self.assertEqual(out["error_code"], "refused")
        self.assertIsInstance(out["latency_ms"], float)

    async def test_timeout(self):
        out = await self._run(OSError("no route"), _Client())
        self.assertEqual(out["reachable"], False)
        self.assertEqual(out["error_code"], "timeout")

    async def test_http_501(self):
        out = await self._run(None, _Client(_Resp(501)))
        self.assertEqual(out["reachable"], True)
        self.assertEqual(out["service_ok"], False)
        self.assertEqual(out["error_code"], "http:501")

    async def test_healthy_merges_vllm_health_fields(self):
        out = await self._run(
            None, _Client(_Resp(200, {"status": "healthy", "version": "1.3"})))
        self.assertEqual(out["reachable"], True)
        self.assertEqual(out["service_ok"], True)
        self.assertIsNone(out["error_code"])
        self.assertEqual(out["status"], "healthy")
        self.assertEqual(out["version"], "1.3")

    async def test_http_timeout_no_response(self):
        out = await self._run(
            None, _Client(exc=httpx.ReadTimeout("slow")))
        self.assertEqual(out["reachable"], True)
        self.assertEqual(out["service_ok"], False)
        self.assertEqual(out["error_code"], "no_response")

    async def test_unconfigured_host(self):
        config_manager.config = ServerConfig()
        out = await config_manager.get_health()
        self.assertEqual(out["reachable"], True)
        self.assertEqual(out["service_ok"], True)
        self.assertEqual(out["status"], "no server configured")


if __name__ == "__main__":
    unittest.main()
