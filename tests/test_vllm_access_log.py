"""vLLM access log aggregation (接入 panel data source): uvicorn access-line
parsing, per-IP aggregates (same API as RequestLog), dashboard-probe
filtering, and poll-output handling (truncation marker / offset advance).
Plus the 2026-10-24 session boundary: the stats restart on every vLLM
service (re)start — the panel only counts the current instance's access."""
import json
import os
import tempfile
import time
import unittest

from backend.service.vllm_access_log import TRUNC_MARKER, VllmAccessLog

# vLLM 0.32.0 routes the uvicorn access log through its loguru logger:
ACCESS = ('INFO:     10.131.8.27:51234 - '
          '"POST /v1/chat/completions HTTP/1.1" 200 OK')
# Plain uvicorn's format (no INFO: prefix, with the [timestamp] section)
# must keep working.
ACCESS_PLAIN = ('10.131.8.27:51234 - - [23/Oct/2026 11:45:12] '
                '"POST /v1/chat/completions HTTP/1.1" 200 OK')
ACCESS_OTHER = ('INFO:     10.131.12.12:40000 - '
                '"POST /v1/completions HTTP/1.1" 200 OK')


class TestIngestParsing(unittest.TestCase):
    def setUp(self):
        self.log = VllmAccessLog()

    def test_access_line_recorded(self):
        self.log.ingest(ACCESS + "\n")
        rows = self.log.connections()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ip"], "10.131.8.27")
        self.assertEqual(rows[0]["last_method"], "POST")
        self.assertEqual(rows[0]["last_path"], "/v1/chat/completions")
        entries = self.log.requests_for("10.131.8.27")
        self.assertEqual(entries[0]["status"], 200)
        self.assertIsNone(entries[0]["body"])

    def test_plain_uvicorn_line_also_recorded(self):
        self.log.ingest(ACCESS_PLAIN + "\n")
        self.assertEqual(len(self.log.connections()), 1)
        self.assertEqual(self.log.connections()[0]["last_path"], "/v1/chat/completions")

    def test_non_access_lines_ignored(self):
        self.log.ingest(
            "INFO:     Started server process [34156]\n"
            "INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)\n"
            "(APIServer pid=34156) INFO 10-23 13:45:03 [loggers.py:335] "
            "Engine 000: Avg prompt throughput: 4958.2 tokens/s\n"
            + ACCESS + "\n")
        self.assertEqual(len(self.log.connections()), 1)

    def test_partial_line_carried_across_chunks(self):
        self.log.ingest(ACCESS[:30])
        self.assertEqual(self.log.connections(), [])
        self.log.ingest(ACCESS[30:] + "\n")
        self.assertEqual(len(self.log.connections()), 1)

    def test_crlf_line(self):
        self.log.ingest(ACCESS + "\r\n")
        self.assertEqual(len(self.log.connections()), 1)

    def test_dashboard_probe_paths_filtered(self):
        self.log.ingest(
            'INFO:     10.131.1.9:40001 - "GET /health HTTP/1.1" 200 OK\n'
            'INFO:     10.131.1.9:40002 - "GET /metrics HTTP/1.1" 200 OK\n'
            'INFO:     10.131.1.9:40003 - "GET /v1/models HTTP/1.1" 200 OK\n')
        self.assertEqual(self.log.connections(), [])

    def test_query_string_stripped_from_path(self):
        self.log.ingest(
            'INFO:     10.131.8.27:51234 - "GET /v1/completions?x=1 HTTP/1.1" 200\n')
        self.assertEqual(self.log.connections()[0]["last_path"], "/v1/completions")

    def test_ansi_color_codes_stripped(self):
        self.log.ingest(
            "\x1b[1mINFO:\x1b[0m     \x1b[36m10.131.8.27:51234\x1b[0m - "
            "\x1b[32m\"POST /v1/chat/completions HTTP/1.1\"\x1b[0m 200 OK\n")
        self.assertEqual(len(self.log.connections()), 1)
        self.assertEqual(self.log.connections()[0]["last_path"], "/v1/chat/completions")


class TestAggregation(unittest.TestCase):
    """The panel API must stay identical to RequestLog's (same row shape)."""

    def setUp(self):
        self.log = VllmAccessLog()

    def test_connections_aggregate_count_and_order(self):
        self.log.record("1.1.1.1", "GET", "/v1/models", 200)
        time.sleep(0.01)
        self.log.record("1.1.1.1", "POST", "/v1/chat/completions", 200)
        time.sleep(0.01)
        self.log.record("2.2.2.2", "POST", "/v1/chat/completions", 200)
        rows = self.log.connections()
        self.assertEqual(rows[0]["ip"], "2.2.2.2")
        self.assertEqual(rows[1]["ip"], "1.1.1.1")
        self.assertEqual(rows[1]["count"], 2)
        self.assertEqual(rows[1]["last_path"], "/v1/chat/completions")
        self.assertEqual(rows[1]["last_method"], "POST")

    def test_requests_for_ip_newest_first_with_limit(self):
        for i in range(5):
            self.log.record("9.9.9.9", "GET", f"/v1/p{i}", 200)
        rows = self.log.requests_for("9.9.9.9", limit=2)
        self.assertEqual([r["path"] for r in rows], ["/v1/p4", "/v1/p3"])
        self.assertEqual(self.log.requests_for("unknown", limit=5), [])

    def test_ring_buffer_bounds_entries_but_count_keeps_growing(self):
        log = VllmAccessLog(maxlen=3)
        for i in range(5):
            log.record("1.2.3.4", "GET", f"/v1/p{i}", 200)
        self.assertEqual(len(log.requests_for("1.2.3.4", limit=10)), 3)
        self.assertEqual(log.connections()[0]["count"], 5)

    def test_reset_clears_state_and_rewinds_offset(self):
        self.log.ingest(ACCESS + "\n")
        self.log.reset()
        self.assertEqual(self.log.connections(), [])
        self.assertEqual(self.log._offset, 1)
        self.assertEqual(self.log._line_buf, "")


class TestPollOutput(unittest.TestCase):
    """_consume_poll_output: truncation marker + tail offset advance."""

    def test_plain_tail_advances_offset(self):
        log = VllmAccessLog()
        chunk = "abc\n" + ACCESS + "\n"
        log._consume_poll_output(chunk)
        self.assertEqual(log._offset, 1 + len(chunk))
        self.assertEqual(len(log.connections()), 1)

    def test_truncation_marker_only_resets_state(self):
        log = VllmAccessLog()
        log.ingest(ACCESS + "\n")
        log._consume_poll_output(TRUNC_MARKER + "\n")
        self.assertEqual(log.connections(), [])
        self.assertEqual(log._offset, 1)

    def test_truncation_marker_with_fresh_tail(self):
        log = VllmAccessLog()
        log.ingest(ACCESS + "\n")
        tail = ACCESS_OTHER + "\n"
        log._consume_poll_output(TRUNC_MARKER + "\n" + tail)
        rows = log.connections()
        self.assertEqual([r["ip"] for r in rows], ["10.131.12.12"])
        self.assertEqual(log._offset, 1 + len(tail))

    # 2026-10-24 incident regression: the old threshold (`S < offset`)
    # fired on every quiet poll where the file simply has not grown
    # (offset = size+1), resetting the aggregates to the FULL historical
    # file — repeatedly, during the minutes-long vLLM reload window.

    def test_quiet_poll_is_not_a_rotation(self):
        log = VllmAccessLog()
        chunk = ACCESS + "\n"
        log._consume_poll_output(
            f"VDB_SIZE={len(chunk)}\nVDB_BYTES={len(chunk)}\n{chunk}")
        self.assertEqual(len(log.connections()), 1)
        self.assertEqual(log._offset, len(chunk) + 1)
        # Quiet poll: the size is unchanged and the fixed threshold
        # (S < offset-1) emits no marker — the aggregates survive.
        log._consume_poll_output(f"VDB_SIZE={len(chunk)}\nVDB_BYTES=0\n")
        self.assertEqual(len(log.connections()), 1)
        self.assertEqual(log._offset, len(chunk) + 1)

    def test_real_shrink_still_resets(self):
        log = VllmAccessLog()
        chunk = ACCESS + "\n"
        log._consume_poll_output(
            f"VDB_SIZE={len(chunk)}\nVDB_BYTES={len(chunk)}\n{chunk}")
        # The file rotated: the size dropped below the previous end.
        log._consume_poll_output(
            f"{TRUNC_MARKER}\nVDB_SIZE={len(chunk) // 2}\nVDB_BYTES=0\n")
        self.assertEqual(log.connections(), [])
        self.assertEqual(log._offset, 1)

    def test_poll_command_uses_offset_minus_one_threshold(self):
        cmd = VllmAccessLog()._build_poll_command(42)
        self.assertIn('[ "$S" -lt 41 ]', cmd)
        self.assertNotIn('[ "$S" -lt 42 ]', cmd)


class TestSessionStart(unittest.TestCase):
    """2026-10-24 user ruling: every vLLM service (re)start restarts the
    per-IP stats — the panel only counts the current instance's access."""

    def _fresh(self, d):
        return VllmAccessLog(session_file=os.path.join(d, "session.json"))

    def test_mark_session_start_clears_aggregates_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._fresh(d)
            log.ingest(ACCESS + "\n")
            self.assertEqual(len(log.connections()), 1)
            log.mark_session_start()
            self.assertEqual(log.connections(), [])
            self.assertEqual(log.requests_for("10.131.8.27"), [])

    def test_next_poll_drops_stale_tail_and_persists_start_point(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._fresh(d)
            sf = os.path.join(d, "session.json")
            log.ingest(ACCESS + "\n")  # the previous instance's data
            log.mark_session_start()
            stale = ACCESS + "\n"  # tailed from the OLD offset — pre-restart
            log._consume_poll_output(
                f"VDB_SIZE=9999\nVDB_BYTES={len(stale)}\n" + stale)
            # Stale tail dropped; the tail jumped past the file's end.
            self.assertEqual(log.connections(), [])
            self.assertEqual(log._offset, 10000)
            with open(sf, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["offset"], 9999)
            # The next poll counts only fresh-session lines.
            fresh = ACCESS_OTHER + "\n"
            log._consume_poll_output(
                f"VDB_SIZE=10000\nVDB_BYTES={len(fresh)}\n" + fresh)
            self.assertEqual([r["ip"] for r in log.connections()],
                             ["10.131.12.12"])

    def test_dashboard_restart_resumes_from_persisted_start_point(self):
        with tempfile.TemporaryDirectory() as d:
            sf = os.path.join(d, "session.json")
            with open(sf, "w", encoding="utf-8") as f:
                json.dump({"offset": 1234}, f)
            log = VllmAccessLog(session_file=sf)
            log._load_session_offset()
            self.assertEqual(log._offset, 1235)

    def test_missing_session_file_reads_from_beginning(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._fresh(d)
            log._load_session_offset()
            self.assertEqual(log._offset, 1)

    def test_pending_session_start_with_rotation_keeps_fresh_tail(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._fresh(d)
            log.mark_session_start()
            fresh = ACCESS + "\n"
            out = (TRUNC_MARKER + "\n" + f"VDB_SIZE={len(fresh)}\n"
                   f"VDB_BYTES={len(fresh)}\n" + fresh)
            log._consume_poll_output(out)
            # The fresh file's beginning IS the session boundary: counted.
            self.assertEqual([r["ip"] for r in log.connections()],
                             ["10.131.8.27"])
            self.assertEqual(log._offset, 1 + len(fresh))
            with open(os.path.join(d, "session.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["offset"], 0)

    def test_rotation_invalidates_persisted_start_point(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._fresh(d)
            sf = os.path.join(d, "session.json")
            with open(sf, "w", encoding="utf-8") as f:
                json.dump({"offset": 50}, f)
            fresh = ACCESS_OTHER + "\n"
            out = (TRUNC_MARKER + "\n" + f"VDB_SIZE={len(fresh)}\n"
                   f"VDB_BYTES={len(fresh)}\n" + fresh)
            log._consume_poll_output(out)
            self.assertEqual([r["ip"] for r in log.connections()],
                             ["10.131.12.12"])
            self.assertFalse(os.path.exists(sf))

    def test_pending_session_start_keeps_flag_on_malformed_round(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._fresh(d)
            log.mark_session_start()
            stale = ACCESS + "\n"  # no VDB_SIZE marker — malformed round
            log._consume_poll_output(stale)
            # Chunk dropped, flag still pending; the offset does NOT
            # advance, so the next poll re-tails from the same offset and
            # its VDB_SIZE reports the true file end.
            self.assertEqual(log.connections(), [])
            self.assertTrue(log._skip_to_end)
            self.assertEqual(log._offset, 1)
            # The next well-formed poll lands the skip — its chunk is the
            # pre-restart tail (dropped), the tail jumps past the file end.
            old_tail = ACCESS + "\n"
            log._consume_poll_output(
                f"VDB_SIZE=777\nVDB_BYTES={len(old_tail)}\n" + old_tail)
            self.assertEqual(log.connections(), [])
            self.assertFalse(log._skip_to_end)
            self.assertEqual(log._offset, 778)
            with open(os.path.join(d, "session.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["offset"], 777)
            # Only after the skip landed do fresh lines count.
            fresh = ACCESS_OTHER + "\n"
            log._consume_poll_output(
                f"VDB_SIZE={777 + len(old_tail)}\n"
                f"VDB_BYTES={len(fresh)}\n" + fresh)
            self.assertEqual([r["ip"] for r in log.connections()],
                             ["10.131.12.12"])


if __name__ == "__main__":
    unittest.main()
