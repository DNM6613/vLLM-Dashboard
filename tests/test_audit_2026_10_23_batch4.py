"""Audit 2026-10-23 batch 4 — the remaining 20 P3 items:

E-5 (--require-hashes in the Docker + CI pip installs),
E-7 (the verify report-only probes carry the API-key header),
E-8 (deploy lock-file mutex), E-11 (start.sh dead-backend aborts),
B-4 (deployment log pruning), A-4 (access-log offset advances by the raw
byte count the remote reports), A-6 (delivered kill + verify timeout still
marks the model stopped), A-7 (watchdog loop survives a crashed tick /
restart attempt), A-8 (stop_polling joins the poller thread),
C-4 (launch-config dir/file chmod), C-5 (WS frame-size bounds),
C-6 (query parameter length caps), C-7 (README security notes)."""
import asyncio
import contextlib
import inspect
import json
import os
import re
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from backend.config.model_launch_config import ModelLaunchConfigManager
from backend.schemas.model import ModelStatus
from backend.service import (
    deployment_service as ds,
)

# model_service must be imported before model_watchdog: model_watchdog
# binds it at module level while model_service imports model_watchdog back
# (a model_watchdog-first import deadlocks on the partial module).
from backend.service import (
    model_service,  # noqa: F401
    model_watchdog,
)
from backend.service.vllm_access_log import TRUNC_MARKER, VllmAccessLog
from tools import server_deploy as sd

# model_lifecycle binds the facade on import — it must be imported only
# after the facade is fully loaded (circular from-import otherwise).
from backend.service import model_lifecycle  # isort: skip

ROOT = Path(__file__).resolve().parent.parent

ACCESS = ('INFO:     10.131.8.27:51234 - '
          '"POST /v1/chat/completions HTTP/1.1" 200 OK')
ACCESS_OTHER = ('INFO:     10.131.12.12:40000 - '
                '"POST /v1/completions HTTP/1.1" 200 OK')


# ---------------------------------------------------------------------------
# E-5: pip --require-hashes (Docker + CI)
# ---------------------------------------------------------------------------

class TestPipHashMode(unittest.TestCase):
    def test_every_lock_entry_has_sha256_hash(self):
        entries: dict[str, int] = {}
        current = None
        for raw in (ROOT / "requirements-lock.txt").read_text(
                encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip().rstrip("\\").strip()
            if not line:
                continue
            m = re.match(r"^([A-Za-z0-9._-]+)==([\w.+-]+)$", line)
            if m:
                current = m.group(1).lower()
                entries.setdefault(current, 0)
            elif line.startswith("--hash=") and current is not None:
                entries[current] += 1
        self.assertGreaterEqual(len(entries), 30)
        self.assertEqual([k for k, v in entries.items() if v == 0], [])

    def test_dockerfile_and_ci_use_require_hashes(self):
        self.assertIn(
            "--require-hashes", (ROOT / "Dockerfile").read_text(encoding="utf-8"))
        self.assertIn(
            "--require-hashes",
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# E-7: verify report-only probes carry the API-key header
# ---------------------------------------------------------------------------

class TestVerifyKeyHeader(unittest.TestCase):
    def test_api_get_cmd_carries_key_header(self):
        with mock.patch.object(sd, "API_KEY", "secret"):
            cmd = sd._api_get_cmd("/api/v1/models")
        self.assertIn("X-API-Key", cmd)
        self.assertIn("/api/v1/models", cmd)
        self.assertTrue(cmd.rstrip().endswith("head -c 400"))

    def test_api_get_cmd_without_key_is_plain(self):
        with mock.patch.object(sd, "API_KEY", ""):
            cmd = sd._api_get_cmd("/api/v1/config/server/status")
        self.assertNotIn("--header", cmd)
        self.assertIn("/api/v1/config/server/status", cmd)


# ---------------------------------------------------------------------------
# E-8: deploy lock-file mutex
# ---------------------------------------------------------------------------

class TestDeployLock(unittest.TestCase):
    def tearDown(self):
        if sd._deploy_lock_fh is not None:
            sd._deploy_lock_fh.close()
            sd._deploy_lock_fh = None

    def test_second_acquire_fails_and_release_reacquires(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sd, "REPO_ROOT", d):
            sd._acquire_deploy_lock()
            with self.assertRaises(SystemExit):
                sd._acquire_deploy_lock()
            sd._deploy_lock_fh.close()
            sd._deploy_lock_fh = None
            sd._acquire_deploy_lock()
            # close inside the with: the temp dir cleanup cannot run
            # while the lock file is still held (Windows)
            sd._deploy_lock_fh.close()
            sd._deploy_lock_fh = None

    def test_main_acquires_lock(self):
        self.assertIn("_acquire_deploy_lock()", inspect.getsource(sd.main))


# ---------------------------------------------------------------------------
# E-11: start.sh distinguishes a dead backend from a slow one
# ---------------------------------------------------------------------------

class TestStartShDeadBackend(unittest.TestCase):
    def test_dead_backend_aborts_before_the_frontend_section(self):
        text = (ROOT / "start.sh").read_text(encoding="utf-8")
        self.assertIn("backend_dead=true", text)
        self.assertIn("The frontend is NOT started", text)
        self.assertLess(text.index("exit 1"), text.index("[2/2] Frontend"))


# ---------------------------------------------------------------------------
# B-4: deployment log pruning
# ---------------------------------------------------------------------------

class TestLogPruning(unittest.TestCase):
    def test_prunes_to_newest_20(self):
        with tempfile.TemporaryDirectory() as d:
            log_dir = os.path.join(d, "logs")
            os.makedirs(log_dir)
            for i in range(25):
                p = os.path.join(log_dir, f"task-{i:02d}.log")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x")
            old = time.time() - 10_000
            for i in range(5):
                p = os.path.join(log_dir, f"task-{i:02d}.log")
                os.utime(p, (old, old))
            with mock.patch.object(ds, "DATA_DIR", d), \
                 mock.patch.object(ds, "STATE_FILE", os.path.join(d, "s.json")), \
                 mock.patch.object(ds, "TASKS_FILE", os.path.join(d, "t.json")), \
                 mock.patch.object(ds, "LOG_DIR", log_dir):
                ds.DeploymentTaskManager()
            remaining = os.listdir(log_dir)
            self.assertEqual(len(remaining), 20)
            for i in range(5):
                self.assertNotIn(f"task-{i:02d}.log", remaining)


# ---------------------------------------------------------------------------
# A-4: offset advances by the raw byte count the remote reports
# ---------------------------------------------------------------------------

class TestByteMarkerOffset(unittest.TestCase):
    def test_offset_advances_by_reported_bytes(self):
        log = VllmAccessLog()
        log._consume_poll_output("VDB_BYTES=40\nabc\n")
        self.assertEqual(log._offset, 1 + 40)

    def test_invalid_utf8_chunk_no_drift(self):
        # U+FFFD re-encodes to 3 bytes each — the old estimate (len of the
        # re-encoded text) would have advanced 6 bytes for a 3-byte chunk.
        log = VllmAccessLog()
        log._consume_poll_output("VDB_BYTES=3\n\uFFFD\uFFFD\n")
        self.assertEqual(log._offset, 1 + 3)

    def test_marker_stripped_before_ingest(self):
        log = VllmAccessLog()
        line = ACCESS + "\n"
        n = len(line.encode("utf-8"))
        log._consume_poll_output(f"VDB_BYTES={n}\n{line}")
        self.assertEqual(len(log.connections()), 1)
        self.assertEqual(log._offset, 1 + n)

    def test_trunc_marker_then_bytes_line(self):
        log = VllmAccessLog()
        log.ingest(ACCESS + "\n")
        tail = ACCESS_OTHER + "\n"
        n = len(tail.encode("utf-8"))
        log._consume_poll_output(
            TRUNC_MARKER + "\n" + f"VDB_BYTES={n}\n" + tail)
        self.assertEqual([r["ip"] for r in log.connections()], ["10.131.12.12"])
        self.assertEqual(log._offset, 1 + n)

    def test_poll_command_reports_byte_count(self):
        cmd = VllmAccessLog()._build_poll_command(5)
        self.assertIn("VDB_BYTES=", cmd)
        self.assertIn("wc -c", cmd)
        self.assertIn("LC_ALL=C", cmd)

    def test_empty_chunk_only_marker(self):
        log = VllmAccessLog()
        log._consume_poll_output("VDB_BYTES=0\n")
        self.assertEqual(log._offset, 1)


# ---------------------------------------------------------------------------
# A-6: delivered kill + verify timeout still marks the model stopped
# ---------------------------------------------------------------------------

class TestStopModelVerifyTimeout(unittest.TestCase):
    def _run_stop(self, stop_vllm_result, verify_result):
        executor = mock.MagicMock()
        executor.stop_vllm = lambda force, model_path: stop_vllm_result

        async def fake_wait(executor_, max_wait=8, model_path=""):
            return verify_result

        model = mock.MagicMock()
        model.path = "/models/m1"

        async def scenario():
            with mock.patch.object(model_lifecycle._ms, "get_remote_executor",
                                   return_value=executor), \
                 mock.patch.object(model_lifecycle._ms, "state_machine") as sm, \
                 mock.patch.object(model_lifecycle._ms, "_watchdog_clear") as wd, \
                 mock.patch.object(model_lifecycle, "_wait_for_remote_stop",
                                   new=fake_wait), \
                 mock.patch.object(model_lifecycle, "_unset_launch_env_vars",
                                   new=mock.AsyncMock()):
                sm.get_model.return_value = model
                return await model_lifecycle.stop_model("m1"), sm, wd

        result, sm, wd = asyncio.run(scenario())
        self.assertNotIn("m1", model_lifecycle._stopping_models)
        return result, sm, wd

    def test_killed_but_verify_timeout_marks_stopped(self):
        result, sm, wd = self._run_stop(
            {"success": True, "mode": "scoped", "error": None}, False)
        sm.update_model_status.assert_called_once_with("m1", ModelStatus.STOPPED)
        sm.clear_current_model.assert_called_once_with("m1")
        wd.assert_called_once_with("m1")
        self.assertEqual(result["status"], "timeout")

    def test_failed_kill_keeps_status(self):
        result, sm, wd = self._run_stop(
            {"success": False, "mode": "scoped", "error": "no match"}, False)
        sm.update_model_status.assert_not_called()
        wd.assert_not_called()
        self.assertEqual(result["status"], "timeout")
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# A-7: watchdog loop survives a crashed tick / restart attempt
# ---------------------------------------------------------------------------

class TestWatchdogGuards(unittest.TestCase):
    def tearDown(self):
        model_watchdog._crash_events.pop("m1", None)

    def test_tick_survives_restart_crash(self):
        async def scenario():
            model = mock.MagicMock()
            model.locked = True
            with mock.patch.object(model_watchdog.state_machine,
                                   "get_all_models", return_value=[model]), \
                 mock.patch.object(model_watchdog.state_machine,
                                   "get_model", return_value=model), \
                 mock.patch.object(model_watchdog._ms, "_sync_status_once",
                                   new=mock.AsyncMock()), \
                 mock.patch.object(model_watchdog, "_attempt_restart",
                                   side_effect=RuntimeError("boom")):
                model_watchdog._crash_events["m1"] = [(datetime.now(), False)]
                await model_watchdog.watchdog_tick()  # must not raise
        asyncio.run(scenario())

    def test_loop_survives_tick_crash(self):
        async def scenario():
            task = None
            with mock.patch.object(model_watchdog, "_stopping", False), \
                 mock.patch.object(model_watchdog, "watchdog_tick",
                                   side_effect=RuntimeError("boom")):
                task = asyncio.create_task(model_watchdog._watchdog_loop())
                await asyncio.sleep(0.05)
                self.assertFalse(task.done())
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# A-8: stop_polling joins the poller thread
# ---------------------------------------------------------------------------

class TestStopPollingJoins(unittest.TestCase):
    def test_stop_waits_for_thread_exit(self):
        from backend.config import remote_client
        log = VllmAccessLog()
        with mock.patch.object(remote_client, "config_manager") as cm:
            cm.get_config.return_value.is_remote.return_value = False
            log.start_polling()
            thread = log._poll_thread
            self.assertIsNotNone(thread)
            self.assertTrue(thread.is_alive())
            log.stop_polling()
        self.assertFalse(thread.is_alive())


# ---------------------------------------------------------------------------
# C-4: launch-config hardening (chmod)
# ---------------------------------------------------------------------------

class TestLaunchConfigHardening(unittest.TestCase):
    def test_init_chmods_base_dir_700(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(os, "chmod") as chmod:
            ModelLaunchConfigManager(base_dir=d)
        self.assertIn(0o700, [c.args[1] for c in chmod.call_args_list])
        self.assertTrue(
            any(str(c.args[0]) == str(Path(d)) for c in chmod.call_args_list))

    def test_save_chmods_file_600(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(os, "chmod") as chmod:
            mgr = ModelLaunchConfigManager(base_dir=d)
            self.assertTrue(
                mgr.save_config("m1", {"start_command": "vllm serve"}))
            self.assertTrue(os.path.exists(os.path.join(d, "m1.json")))
        self.assertIn(0o600, [c.args[1] for c in chmod.call_args_list])


# ---------------------------------------------------------------------------
# C-5: websocket frame-size bounds
# ---------------------------------------------------------------------------

class TestHardwareWsFrameBound(unittest.TestCase):
    def test_oversized_frame_closes(self):
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        from backend.main import app
        client = TestClient(app)
        with client.websocket_connect("/ws/hardware") as ws:
            ws.send_text("x" * (70 * 1024))
            # The broadcast task may have already delivered a gpu_metrics
            # frame before the oversized one was processed — drain until
            # the server's close(1009) arrives.
            closed = False
            for _ in range(20):
                try:
                    ws.receive_text()
                except WebSocketDisconnect:
                    closed = True
                    break
            self.assertTrue(closed, "server did not close the connection")


class TestConsoleFrameBound(unittest.TestCase):
    def test_oversized_frame_rejected_before_json(self):
        import backend.console.session_manager as sm_mod

        class _FakeWs:
            query_params = {}

            def __init__(self):
                self.sent = []
                self.closed_code = None
                frames = iter([
                    {"type": "websocket.receive", "text": "a" * 70_000},
                    {"type": "websocket.disconnect"},
                ])

                async def receive():
                    return next(frames)
                self.receive = receive

            async def accept(self):
                pass

            async def close(self, code=None):
                self.closed_code = code

            async def send_text(self, text):
                self.sent.append(json.loads(text))

        async def scenario():
            session = mock.MagicMock()
            session.is_alive.return_value = True
            session.get_history_with_position.return_value = ("", 0)
            session.total_written.return_value = 0
            session.get_output_since.return_value = ("", 0, False)
            cfg = mock.MagicMock()
            cfg.is_remote.return_value = True
            cfg.host = "10.0.0.7"
            ws = _FakeWs()
            with mock.patch.object(sm_mod, "ws_api_key_valid",
                                   return_value=True), \
                 mock.patch.object(sm_mod, "config_manager") as cm, \
                 mock.patch.object(sm_mod, "console_session_manager") as csm:
                cm.get_config.return_value = cfg
                csm.get_or_create.return_value = (session, True)
                await sm_mod.websocket_console(ws)
            return ws

        ws = asyncio.run(scenario())
        self.assertIn(
            {"type": "error", "data": "Input too large (max 64KB)"}, ws.sent)
        self.assertNotIn(
            {"type": "error", "data": "Invalid JSON format"}, ws.sent)


# ---------------------------------------------------------------------------
# C-6: query parameter length caps
# ---------------------------------------------------------------------------

class TestQueryLengthCaps(unittest.TestCase):
    def test_overlong_params_422(self):
        from fastapi.testclient import TestClient

        from backend.main import app
        client = TestClient(app)
        self.assertEqual(
            client.get("/api/v1/models/download/status",
                       params={"log_file": "a" * 300}).status_code, 422)
        self.assertEqual(
            client.get("/api/v1/models/download/status",
                       params={"log_file": "/tmp/model_download_x.log",
                               "repo": "b" * 300}).status_code, 422)
        self.assertEqual(
            client.post("/api/v1/models/start",
                        params={"model_id": "c" * 200}).status_code, 422)
        self.assertEqual(
            client.post("/api/v1/models/download",
                        params={"model_repo": "d" * 300}).status_code, 422)


# ---------------------------------------------------------------------------
# C-7: README security notes
# ---------------------------------------------------------------------------

class TestReadmeSecurityNotes(unittest.TestCase):
    def test_both_readmes_carry_the_tls_note(self):
        for name in ("README.md", "README-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("TLS", text, name)


if __name__ == "__main__":
    unittest.main()
