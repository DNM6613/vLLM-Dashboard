"""Audit 2026-10-22 (docs/audit-report-2026-10-22.md) batch-3 fixes.

Covers: BCK-6 (complete_driver idempotency), BCK-8 (download-status
in-flight dedup without a TTL cache), BCK-9 (shutdown swallows only the
expected CancelledError, real errors are logged), BCK-13 (pending
start-verification probes cancelled on shutdown), REM-6 (reattach terminal
branches clean the sudo-password temp file), REM-7 (pid file trusted only
when /proc/<pid>/cmdline still names the repo), REM-11 (lingering
awaiting_reboot driver tasks superseded), REM-12 (cmdline re-check before
killing a remote process tree), SEC-2 (launch-config body gated out of the
access log), SEC-3 (docs/redoc/openapi endpoints closed). REM-10 (URL query
charset) is appended to test_deployment_url_tail.py.
"""
import asyncio  # noqa: I001 (backend imports below are deliberately non-alphabetical)
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.service import deployment_service as ds
# model_service must be imported before model_download_status (NOT in
# alphabetical order as I001 would demand — a noqa on the first import line
# suppresses that): model_download_status imports model_service at module
# level, and the chain (model_service -> model_downloads ->
# model_download_status) only resolves cleanly when the first entry point
# is model_service. Reversed, a standalone run of this file hits the
# circular import at model_download_status's own import of model_service.
from backend.service import model_service
from backend.service import model_download_status
from backend.ws.hardware_broadcaster import HardwareBroadcaster

LOG_FILE = "/tmp/batch3_dl_repo.log"


def _status_stdout(pid: str, mtime: int, tail: str, log_size: int = 12345) -> str:
    head = f"EXISTS\nSIZE={log_size}\nMTIME={mtime}\nPID={pid}\n"
    return head + "---TAIL---\n" + tail


class _MgrTestCase(unittest.TestCase):
    """DeploymentTaskManager against a scratch data dir (same pattern as
    test_deployment_service.py TestTaskManager)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._patch = patch.multiple(
            ds,
            STATE_FILE=os.path.join(self.tmp.name, "state.json"),
            TASKS_FILE=os.path.join(self.tmp.name, "tasks.json"),
            DATA_DIR=self.tmp.name,
            LOG_DIR=os.path.join(self.tmp.name, "logs"),
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()


class TestCompleteDriverIdempotent(_MgrTestCase):
    """BCK-6: the 30 s reconcile timer and the /drivers read endpoint can
    both complete the same task — the second call must be a no-op (no
    duplicate history entry, no re-flip)."""

    def test_second_completion_is_noop(self):
        mgr = ds.DeploymentTaskManager()
        task = mgr.create("driver", "reboot", ["s1", "s2"])
        task.status = "awaiting_reboot"
        before = len(ds.deployment_state.get()["history"])
        mgr.complete_driver(task)
        self.assertEqual(task.status, "success")
        mgr.complete_driver(task)
        self.assertEqual(task.status, "success")
        self.assertEqual(len(ds.deployment_state.get()["history"]) - before, 1)

    def test_non_awaiting_task_untouched(self):
        mgr = ds.DeploymentTaskManager()
        task = mgr.create("driver", "done", ["s1"])
        task.status = "success"
        task.finished_at = "2026-09-01 00:00:00"
        mgr.complete_driver(task)
        self.assertEqual(task.status, "success")
        self.assertEqual(task.finished_at, "2026-09-01 00:00:00")


class TestSupersedeAwaitingDriver(_MgrTestCase):
    """REM-11: a newer driver install must force-finish the older pending
    post-reboot task (the reconcile only completes the newest target_major,
    so the older one would stay 'awaiting verification' forever)."""

    def test_lingering_awaiting_tasks_are_failed(self):
        mgr = ds.DeploymentTaskManager()
        t1 = mgr.create("driver", "old", ["s"])
        t1.status = "success"  # unblock the kind so t2 can be created
        t2 = mgr.create("driver", "newer", ["s"])
        t1.status = "awaiting_reboot"
        t2.status = "awaiting_reboot"
        before = len(ds.deployment_state.get()["history"])
        mgr.supersede_awaiting_driver()
        for t in (t1, t2):
            self.assertEqual(t.status, "failed")
            self.assertIn("Superseded", t.error)
            self.assertTrue(t.finished_at)
        self.assertIsNone(mgr.pending_driver_task())
        self.assertEqual(len(ds.deployment_state.get()["history"]) - before, 2)

    def test_noop_without_pending_tasks(self):
        mgr = ds.DeploymentTaskManager()
        t = mgr.create("driver", "ok", ["s"])
        t.status = "success"
        before = len(ds.deployment_state.get()["history"])
        mgr.supersede_awaiting_driver()
        self.assertEqual(t.status, "success")
        self.assertEqual(len(ds.deployment_state.get()["history"]), before)


class TestKillLongAndReattachCleanup(_MgrTestCase):
    """REM-12: a recycled pid must not have its tree killed (cmdline
    re-check). REM-6: the sudo-password temp file is part of the remote
    job footprint — every terminal branch deletes it."""

    def test_kill_long_rechecks_cmdline_before_signalling(self):
        mgr = ds.DeploymentTaskManager()
        ex = MagicMock()
        with patch.object(ds, "get_remote_executor", return_value=ex):
            mgr._kill_long("dep_x", "/tmp/vdb_deploy_dep_x.log.pid")
        cmd = ex.execute.call_args[0][0]
        self.assertIn("/proc/$P/cmdline", cmd)
        self.assertIn("grep -qF", cmd)
        self.assertIn("kill -TERM", cmd)
        self.assertIn("VDB_KILLED", cmd)

    def test_reattach_terminal_branch_cleans_pw_file(self):
        mgr = ds.DeploymentTaskManager()
        task = mgr.create("vllm", "reattach", ["s"])
        task.status = "interrupted"
        job = {
            "log": "/tmp/vdb.log",
            "pid": "/tmp/vdb.log.pid",
            "exit": "/tmp/vdb.log.exit",
            "pw": "/tmp/.vdb_pw_dep_x",
        }
        ex = MagicMock()

        def execute(cmd, timeout=None, stdin_data=None):
            if cmd.startswith("tail -c"):
                return {"success": True,
                        "stdout": "line\n\nVDB_POLL\nVDB_EXIT:0\n", "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        ex.execute.side_effect = execute
        cleaned = []
        with patch.object(ds, "get_remote_executor", return_value=ex), \
             patch.object(mgr, "_cleanup",
                          side_effect=lambda tid, *p: cleaned.append(p)):
            mgr._watch_remote_job(task, job)
        self.assertEqual(cleaned, [
            ("/tmp/vdb.log", "/tmp/vdb.log.pid", "/tmp/vdb.log.exit",
             "/tmp/.vdb_pw_dep_x")])
        self.assertEqual(task.status, "failed")


class TestDownloadStatusCmdlineRecheck(unittest.TestCase):
    """REM-7: the pid file is authoritative only while
    /proc/<pid>/cmdline still names this repo — otherwise a recycled pid
    keeps the status 'downloading' forever."""

    def test_pid_trusted_only_when_cmdline_names_repo(self):
        cmd = model_download_status._build_download_status_command(
            LOG_FILE, repo="org/big-model")
        self.assertIn("/proc/$WP/cmdline", cmd)
        self.assertIn("grep -qF", cmd)
        self.assertIn("org/big-model", cmd)
        # the ps|grep fallback is kept for a missing/stale pid file
        self.assertIn("ps -eo", cmd)

    def test_without_repo_plain_liveness(self):
        cmd = model_download_status._build_download_status_command(LOG_FILE)
        self.assertIn("kill -0", cmd)
        self.assertNotIn("/proc/$WP/cmdline", cmd)


class TestDownloadStatusInflightDedup(unittest.TestCase):
    """BCK-8: N open tabs polling the same log file share one SSH burst;
    sequential polls must still recompute (no post-hoc TTL cache)."""

    def setUp(self):
        model_service._dl_growth.clear()
        model_service._dl_total_cache.clear()
        model_download_status._dl_status_inflight.clear()
        model_download_status._dl_status_last.clear()

    def _executor(self) -> MagicMock:
        ex = MagicMock()
        ex.get_download_size.return_value = {"success": True, "size_bytes": 1024}
        ex.get_download_total_size.return_value = {"success": True, "total_bytes": 4096}
        return ex

    def test_concurrent_calls_share_one_ssh_query(self):
        ex = self._executor()
        entered = threading.Event()
        release = threading.Event()
        slow = {"success": True,
                "stdout": _status_stdout(pid="777", mtime=int(time.time()),
                                         tail="Fetching: 5%"),
                "stderr": ""}

        def slow_execute(cmd, *a, **k):
            entered.set()
            release.wait(timeout=5)
            return slow

        ex.execute.side_effect = slow_execute
        with patch("backend.service.model_service.get_remote_executor",
                   return_value=ex):
            results, errors = [], []

            def worker():
                try:
                    results.append(model_service.get_download_status(
                        LOG_FILE, repo="org/name", save_path="~/m"))
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(3)]
            for th in threads:
                th.start()
            self.assertTrue(entered.wait(5))
            time.sleep(0.2)  # let the other threads enter the wait path
            release.set()
            for th in threads:
                th.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 3)
        self.assertEqual(ex.execute.call_count, 1)
        self.assertEqual(results[0]["status"], "downloading")
        self.assertIs(results[1], results[0])
        self.assertIs(results[2], results[0])

    def test_sequential_polls_always_recompute(self):
        ex = self._executor()
        ex.execute.return_value = {"success": True,
                                   "stdout": _status_stdout(pid="", mtime=int(time.time()), tail=""),
                                   "stderr": ""}
        with patch("backend.service.model_service.get_remote_executor",
                   return_value=ex):
            model_service.get_download_status(LOG_FILE, repo="org/name", save_path="~/m")
            model_service.get_download_status(LOG_FILE, repo="org/name", save_path="~/m")
        self.assertEqual(ex.execute.call_count, 2)


class TestBroadcasterShutdownErrorHandling(unittest.TestCase):
    """BCK-9: shutdown swallows only the expected CancelledError; a real
    error in the stop path must surface in the log, not vanish."""

    def test_real_error_is_logged_not_swallowed(self):
        async def scenario():
            bc = HardwareBroadcaster()

            async def broken():
                try:
                    await asyncio.sleep(30)
                finally:
                    raise RuntimeError("cleanup bug")

            bc._poll_task = asyncio.get_running_loop().create_task(broken())
            # Let the task run its first step (suspend in sleep) BEFORE the
            # shutdown. Probe (Python 3.14, 2026-10-22): a task cancelled
            # before its first step is silently cancelled — its body, and
            # hence the finally error, never runs — which is the expected
            # branch, not a real stop-path failure.
            await asyncio.sleep(0)
            with self.assertLogs("backend.ws.hardware_broadcaster",
                                 level="ERROR") as cm:
                await bc.shutdown()
            self.assertTrue(any("failed to stop cleanly" in line
                                for line in cm.output))

        asyncio.run(scenario())

    def test_expected_cancellation_is_silent(self):
        async def scenario():
            bc = HardwareBroadcaster()

            async def normal():
                await asyncio.sleep(30)

            bc._poll_task = asyncio.get_running_loop().create_task(normal())
            await bc.shutdown()  # must not raise

        asyncio.run(scenario())


class TestStopStartProbes(unittest.TestCase):
    """BCK-13: the 10 s delayed start-verification probes are plain asyncio
    tasks — shutdown must cancel them so they stop poking the remote."""

    def test_pending_probes_are_cancelled(self):
        async def scenario():
            async def probe():
                await asyncio.sleep(60)

            task = asyncio.get_running_loop().create_task(probe())
            model_service._start_probe_tasks.add(task)
            try:
                model_service.stop_start_probes()
                await asyncio.sleep(0.01)
                self.assertTrue(task.cancelled())
            finally:
                model_service._start_probe_tasks.discard(task)

        asyncio.run(scenario())


class TestDocsEndpointsClosed(unittest.TestCase):
    """SEC-3: /docs, /redoc and /openapi.json serve the full route listing —
    all three are disabled (user ruling: close all)."""

    def test_all_doc_endpoints_disabled(self):
        from backend.main import app
        self.assertIsNone(app.docs_url)
        self.assertIsNone(app.redoc_url)
        self.assertIsNone(app.openapi_url)


class TestCredentialBodyGate(unittest.TestCase):
    """SEC-2: the launch-config body can carry --api-key / VLLM_API_KEY —
    it must not enter the access log that the connections panel serves
    verbatim."""

    def test_launch_config_is_a_credential_path(self):
        from backend.middleware import _CREDENTIAL_BODY_PATHS
        self.assertIn("/api/v1/models/launch-config", _CREDENTIAL_BODY_PATHS)
        self.assertIn("/api/v1/config/server", _CREDENTIAL_BODY_PATHS)

    def test_launch_config_body_not_captured(self):
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        from backend.middleware import request_log_middleware
        from backend.service.request_log import request_log

        body = b'{"start_command": "vllm serve --api-key SECRET"}'

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        scope = {
            "type": "http", "method": "POST",
            "path": "/api/v1/models/launch-config",
            "headers": [(b"content-length", str(len(body)).encode())],
            "client": ("1.2.3.4", 1234),
        }
        request = Request(scope, receive)

        async def call_next(req):
            return JSONResponse({"ok": True})

        request_log.reset()
        try:
            asyncio.run(request_log_middleware(request, call_next))
            rows = request_log.requests_for("1.2.3.4")
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["body"])
        finally:
            request_log.reset()


if __name__ == "__main__":
    unittest.main()
