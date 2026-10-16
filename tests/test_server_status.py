"""Regression for /api/v1/config/server/status → check_connection.

3a4c8d0 split moved ``DetailedConnectionStatus`` from ``remote_client.py``
to ``config/server_config.py``, but ``ConnectionProbeMixin.check_connection``
kept referencing it through the facade (``_rc.DetailedConnectionStatus``),
so every poll of the endpoint 500'd with AttributeError in production
(2026-10-16). Locks that the call returns the model instead of raising.
"""
import asyncio
import unittest

from backend.config.remote_client import config_manager
from backend.config.server_config import (
    ConnectionDetail,
    DetailedConnectionStatus,
    ServerConfig,
    ServerStatus,
)


class TestCheckConnectionReturnsModel(unittest.TestCase):
    def setUp(self):
        self._saved = config_manager.config
        config_manager.config = ServerConfig()

    def tearDown(self):
        config_manager.config = self._saved

    def test_no_host_returns_detailed_status_without_raising(self):
        result = asyncio.run(config_manager.check_connection())
        self.assertIsInstance(result, DetailedConnectionStatus)
        self.assertIsNone(result.http)
        self.assertIsNone(result.ssh)
        self.assertEqual(result.overall, ServerStatus.UNKNOWN)

    def test_host_configured_aggregates_probe_results(self):
        config_manager.config = ServerConfig(host="10.0.0.1")

        async def _fake_http():
            return "http", ConnectionDetail(
                status=ServerStatus.CONNECTED,
                latency_ms=5.0,
                version="1 个模型已加载",
            )

        config_manager._check_http = _fake_http
        try:
            result = asyncio.run(config_manager.check_connection())
        finally:
            del config_manager.__dict__["_check_http"]

        self.assertEqual(result.overall, ServerStatus.CONNECTED)
        self.assertEqual(result.http.version, "1 个模型已加载")
        self.assertIsInstance(result.model_dump(), dict)


if __name__ == "__main__":
    unittest.main()
