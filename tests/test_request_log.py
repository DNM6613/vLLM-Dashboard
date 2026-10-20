"""Access log (Q2 接入情况): RequestLog service, /api/v1/monitor API,
and the recording middleware (incl. body capture and path exclusions)."""
import time
import unittest

from fastapi.testclient import TestClient

from backend.main import app
from backend.middleware import _sanitize_body
from backend.service import request_log as rl
from backend.service.request_log import RequestLog


class TestRequestLogService(unittest.TestCase):
    def test_connections_aggregate_count_and_order(self):
        log = RequestLog()
        # Distinct ticks: last_seen is a float timestamp and the order
        # assertion needs strictly increasing values. The most recently
        # active IP (2.2.2.2, recorded last) must sort first.
        log.record("1.1.1.1", "GET", "/api/v1/models", 200)
        time.sleep(0.01)
        log.record("1.1.1.1", "GET", "/api/v1/hardware/metrics", 200)
        time.sleep(0.01)
        log.record("2.2.2.2", "POST", "/api/v1/models/chat", 200, "hi")
        rows = log.connections()
        self.assertEqual(rows[0]["ip"], "2.2.2.2")
        self.assertEqual(rows[1]["ip"], "1.1.1.1")
        self.assertEqual(rows[1]["count"], 2)
        self.assertEqual(rows[1]["last_path"], "/api/v1/hardware/metrics")
        self.assertEqual(rows[1]["last_method"], "GET")

    def test_requests_for_ip_newest_first_with_limit(self):
        log = RequestLog()
        for i in range(5):
            log.record("9.9.9.9", "GET", f"/api/v1/x{i}", 200)
        rows = log.requests_for("9.9.9.9", limit=2)
        self.assertEqual([r["path"] for r in rows], ["/api/v1/x4", "/api/v1/x3"])
        self.assertEqual(log.requests_for("unknown", limit=5), [])

    def test_ring_buffer_bounds_entries_but_count_keeps_growing(self):
        log = RequestLog(maxlen=3)
        for i in range(5):
            log.record("1.2.3.4", "GET", f"/api/v1/p{i}", 200)
        self.assertEqual(len(log.requests_for("1.2.3.4", limit=10)), 3)
        self.assertEqual(log.connections()[0]["count"], 5)

    def test_reset_clears_state(self):
        log = RequestLog()
        log.record("1.1.1.1", "GET", "/x", 200)
        log.reset()
        self.assertEqual(log.connections(), [])


class TestSanitizeBody(unittest.TestCase):
    def test_empty_yields_none(self):
        self.assertIsNone(_sanitize_body(b""))

    def test_strips_control_chars_keeps_newlines(self):
        self.assertEqual(_sanitize_body(b"a\x01b\n"), "ab\n")

    def test_caps_at_preview_length(self):
        out = _sanitize_body(b"x" * 5000)
        self.assertEqual(len(out), 2048)

    def test_invalid_utf8_replaced_not_raised(self):
        out = _sanitize_body(b"\xff\xfe ab")
        self.assertIsNotNone(out)
        self.assertIn("ab", out)


class TestMonitorEndpointsAndMiddleware(unittest.TestCase):
    """Full-app TestClient: the middleware must record real /api/v1 traffic
    (with body previews) and skip the panel's own endpoints."""

    def setUp(self):
        rl.request_log.reset()
        self.client = TestClient(app)

    def tearDown(self):
        rl.request_log.reset()

    def test_connections_endpoint_shape(self):
        r = self.client.get("/api/v1/monitor/connections")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"connections": []})

    def test_middleware_records_api_get(self):
        self.client.get("/api/v1/models")
        rows = rl.request_log.connections()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 1)
        self.assertEqual(rows[0]["last_path"], "/api/v1/models")

    def test_middleware_captures_post_body_preview(self):
        # POST to a GET-only route → 405, but the body is still logged.
        r = self.client.post("/api/v1/hardware/metrics", json={"hello": "world"})
        self.assertEqual(r.status_code, 405)
        rows = rl.request_log.requests_for(self.client_ip(), limit=10)
        self.assertTrue(any("hello" in (e["body"] or "") for e in rows))

    def test_monitor_and_non_api_paths_not_recorded(self):
        self.client.get("/health")
        self.client.get("/api/v1/monitor/connections")
        self.assertEqual(rl.request_log.connections(), [])

    def test_requests_endpoint_filters_by_ip(self):
        self.client.get("/api/v1/models")
        ip = self.client_ip()
        r = self.client.get(f"/api/v1/monitor/requests?ip={ip}")
        self.assertEqual(r.status_code, 200)
        rows = r.json()["requests"]
        self.assertTrue(rows)
        self.assertTrue(all(e["ip"] == ip for e in rows))

    @staticmethod
    def client_ip() -> str:
        return "testclient"
