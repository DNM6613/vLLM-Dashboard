"""Audit 2026-10-22 batch 2 regressions.

REM-4: the scoped vLLM stop kills the whole process tree (main ->
EngineCore -> Workers), and the scoped stop-wait CLEAN check flags leaked
VLLM::Worker grandchildren (their cmdline does not carry the model path).
REM-3: a reattached remote job that outlives the 6h watch window is killed
(run_long deadline semantics), not just abandoned.
REM-5: driver and CUDA installs are mutually exclusive (both write the
system: apt / driver packages / nvidia kernel modules).
BCK-3: task_manager.create claims the kind atomically inside the lock
(the entry-level precheck alone leaves a TOCTOU window).
BCK-4: the download probe fails closed, and the check-then-launch runs
atomically on the remote (a duplicate download is reported, not launched).
BCK-5: a manual stop in flight shields the start probe from recording a
crash (no watchdog re-launch of a model the user just stopped).
SEC-1: PUT /deployment/state rejects over-long mirror / selected fields.
INF-3: /health is a readiness probe (degraded 503 when the static assets
or the data dir are missing).
"""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

# backend.main is imported first: its app wiring has already loaded the
# model_* service facade (and its model_service <-> model_chat cycle is
# resolved at that point), so the direct service imports below are safe.
import backend.main as main_mod
from backend.api.deployment_routes_tasks import put_state
from backend.config.remote_client import config_manager
from backend.executor.remote import process_ops
from backend.executor.remote.model_ops import ModelOps, _probe_pid_expr
from backend.schemas.model import ModelStatus
from backend.service import (
    deployment_entry,
    model_downloads,
    model_lifecycle,
)
from backend.service import (
    deployment_service as ds,
)

# ---------------------------------------------------------------------------
# REM-4: scoped stop kills the process tree
# ---------------------------------------------------------------------------

class TestScopedKillTree(unittest.TestCase):
    def test_scoped_stop_command_recurses_children_first(self):
        cmd = process_ops._build_stop_command("/models/m1", "-9")
        # recursive post-order kill function (children before parent)
        self.assertIn("kterm()", cmd)
        self.assertIn('for p in $main_pids; do kterm "$p"; done', cmd)
        self.assertIn('kill -9 "$1" 2>/dev/null; };', cmd)
        # the old single-level main-kill line is gone from the scoped branch
        self.assertNotIn("for p in $main_pids; do kill -9 $p 2>/dev/null; pkill -9 -P $p", cmd)

    def test_global_stop_command_keeps_single_level_pkill(self):
        # known-2 (audit ruling): the global fallback is intentionally
        # unchanged.
        cmd = process_ops._build_stop_command("", "-9")
        self.assertIn("pkill -9 -P", cmd)
        self.assertNotIn("kterm", cmd)

    def test_scoped_wait_command_flags_live_workers(self):
        cmd = model_lifecycle._build_stop_wait_command("/models/m1")
        self.assertIn("VLLM::Worker", cmd)
        self.assertIn("worker_cnt", cmd)
        self.assertIn("grep -cE", cmd)

    def test_global_wait_command_unchanged(self):
        cmd = model_lifecycle._build_stop_wait_command("")
        self.assertNotIn("VLLM::Worker", cmd)


# ---------------------------------------------------------------------------
# REM-3: reattach deadline kills the remote job
# ---------------------------------------------------------------------------

class TestReattachDeadlineKill(unittest.TestCase):
    def test_deadline_kills_remote_job_and_finalizes(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(ds, "TASKS_FILE", os.path.join(tmp.name, "tasks.json")), \
                 mock.patch.object(ds, "DATA_DIR", tmp.name):
                mgr = ds.DeploymentTaskManager()
                task = mgr.create("vllm", "unit-test", ["s1"])
                task.status = "running"
                job = {"log": "/tmp/j.log", "pid": "/tmp/j.pid", "exit": "/tmp/j.exit"}
                task.result = {"job_files": job}
                calls = []

                def fake_execute(cmd, timeout=30):
                    calls.append(cmd)
                    return {"success": True, "stdout": "\nVDB_POLL\nVDB_RUNNING"}

                fake_executor = mock.MagicMock()
                fake_executor.execute = fake_execute
                # monotonic: deadline computation (1000), then the in-loop
                # check (past deadline) — the deadline branch must fire.
                with mock.patch.object(ds, "get_remote_executor", return_value=fake_executor), \
                     mock.patch("time.monotonic", side_effect=[1000.0, 10 ** 9, 10 ** 9]), \
                     mock.patch("time.sleep"):
                    mgr._watch_remote_job(task, dict(job))
                self.assertEqual(task.status, "failed")
                # the whole remote tree was killed (kterm/kkill recursion)
                self.assertTrue(any("kterm" in c for c in calls), calls)
                # the job files were cleaned up
                self.assertTrue(any(c.startswith("rm -f") and "/tmp/j.log" in c for c in calls), calls)
        finally:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# REM-5: driver x CUDA mutual exclusion
# ---------------------------------------------------------------------------

class TestDriverCudaMutualExclusion(unittest.TestCase):
    @staticmethod
    def _inflight(kinds_hit):
        def fake_inflight(kind):
            if kind in kinds_hit:
                t = mock.MagicMock()
                t.kind = kind
                return t
            return None
        return fake_inflight

    def test_driver_refused_when_cuda_inflight(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"cuda"})), \
                self.assertRaises(ds.DeploymentError) as ctx:
            deployment_entry._reject_inflight("driver")
        self.assertIn("CUDA", str(ctx.exception))

    def test_cuda_refused_when_driver_inflight(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"driver"})), \
                self.assertRaises(ds.DeploymentError) as ctx:
            deployment_entry._reject_inflight("cuda")
        self.assertIn("Driver", str(ctx.exception))

    def test_same_kind_guard_unchanged(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"driver"})), \
                self.assertRaises(ds.DeploymentError):
            deployment_entry._reject_inflight("driver")

    def test_vllm_ignores_driver_and_cuda(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"driver", "cuda"})):
            deployment_entry._reject_inflight("vllm")  # no raise


# ---------------------------------------------------------------------------
# BCK-3: create claims the kind atomically
# ---------------------------------------------------------------------------

class TestCreateAtomicClaim(unittest.TestCase):
    def test_second_create_of_inflight_kind_refused(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(ds, "TASKS_FILE", os.path.join(tmp.name, "tasks.json")), \
                 mock.patch.object(ds, "DATA_DIR", tmp.name):
                mgr = ds.DeploymentTaskManager()
                first = mgr.create("driver", "first", ["s"])
                self.assertEqual(first.status, "queued")
                with self.assertRaises(ds.DeploymentError) as ctx:
                    mgr.create("driver", "second", ["s"])
                self.assertIn("driver", str(ctx.exception))
                # audit 2026-10-23 B-2: the claim spans the whole inflight
                # domain — cuda (same domain as driver) is refused too,
                # kinds outside the domain are unaffected.
                with self.assertRaises(ds.DeploymentError) as ctx2:
                    mgr.create("cuda", "cuda task", ["s"])
                self.assertIn("driver", str(ctx2.exception))
                other = mgr.create("vllm", "vllm task", ["s"])
                self.assertEqual(other.status, "queued")
        finally:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# BCK-4: download probe fail-closed + atomic check-then-launch
# ---------------------------------------------------------------------------

class TestDownloadProbeFailClosed(unittest.TestCase):
    @staticmethod
    def _download_ctx(probe_result, launch_result=None):
        executor = mock.MagicMock()
        executor.execute = mock.Mock(return_value=probe_result)
        if launch_result is not None:
            executor.download_model = mock.Mock(return_value=launch_result)
        executor.set_hf_mirror = mock.Mock(return_value={"success": True})
        return executor

    def test_probe_failure_refuses_launch(self):
        executor = self._download_ctx({"success": False, "stdout": "", "stderr": "boom"})
        with mock.patch.object(model_downloads._ms, "get_remote_executor", return_value=executor), \
                self.assertRaises(HTTPException) as ctx:
            model_downloads.download_model("org/repo", "", True)
        self.assertEqual(ctx.exception.status_code, 503)
        executor.download_model.assert_not_called()

    def test_probe_finds_existing_download_409(self):
        executor = self._download_ctx({"success": True, "stdout": "EXIST=123"})
        with mock.patch.object(model_downloads._ms, "get_remote_executor", return_value=executor), \
                self.assertRaises(HTTPException) as ctx:
            model_downloads.download_model("org/repo", "", True)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("123", str(ctx.exception.detail))
        executor.download_model.assert_not_called()

    def test_atomic_launch_duplicate_reports_409(self):
        executor = self._download_ctx(
            {"success": True, "stdout": "EXIST=NONE"},
            launch_result={"success": False, "already_running": "456",
                           "stdout": "VDB_ALREADY:456", "log_file": "/tmp/x.log"},
        )
        with mock.patch.object(model_downloads._ms, "get_remote_executor", return_value=executor), \
                self.assertRaises(HTTPException) as ctx:
            model_downloads.download_model("org/repo", "", True)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("456", str(ctx.exception.detail))

    def test_normal_launch_returns_pid(self):
        executor = self._download_ctx(
            {"success": True, "stdout": "EXIST=NONE"},
            launch_result={"success": True, "stdout": "789", "log_file": "/tmp/x.log"},
        )
        with mock.patch.object(model_downloads._ms, "get_remote_executor", return_value=executor), \
             mock.patch.object(config_manager, "get_config") as cfg:
            cfg.return_value = mock.MagicMock(model_save_path="")
            result = model_downloads.download_model("org/repo", "", True)
        self.assertEqual(result["pid"], 789)


class TestDownloadAtomicCommand(unittest.TestCase):
    def test_launch_rechecks_duplicate_in_same_command(self):
        # ModelOps is a mixin; a bare instance lacks the other mixins'
        # helpers, so stub the ones download_model needs.
        ops = ModelOps.__new__(ModelOps)
        ops._get_activate_cmd = lambda: ""
        ops._escape_double_quoted = lambda s: s
        captured = {}

        def fake_execute(cmd, timeout=60):
            captured["cmd"] = cmd
            # the real executor.execute always returns these keys
            return {"success": True, "stdout": "42", "stderr": ""}

        ops.execute = fake_execute
        with mock.patch.object(config_manager, "get_config") as cfg:
            cfg.return_value = mock.MagicMock(hf_token="")
            result = ops.download_model("org/repo", "", hf_mirror=False)
        cmd = captured["cmd"]
        self.assertIn("VDB_ALREADY:", cmd)
        # the duplicate check precedes the launch inside the ONE command
        self.assertLess(cmd.index('if [ -n "$P" ]'), cmd.index("nohup"))
        self.assertTrue(result["success"])
        self.assertIsNone(result.get("already_running"))

    def test_probe_fragment_filters_its_own_pipeline(self):
        frag = _probe_pid_expr("'org/repo'")
        # the sentinel trick: the probe chain carries the sentinel in its
        # own cmdline and is excluded by the first grep -v.
        self.assertIn("grep -v 'VDB_DL_PROBE_SENTINEL'", frag)
        self.assertIn("hf download|huggingface-cli download", frag)


# ---------------------------------------------------------------------------
# BCK-5: manual stop shields the start probe
# ---------------------------------------------------------------------------

class TestStopStartProbeCoordination(unittest.TestCase):
    @staticmethod
    def _fake_model(status, locked=True):
        m = mock.MagicMock()
        m.status = status
        m.locked = locked
        return m

    def test_probe_skips_rollback_while_stop_in_flight(self):
        async def scenario():
            with mock.patch.object(model_lifecycle._ms, "state_machine") as sm, \
                 mock.patch.object(model_lifecycle._ms, "_check_vllm_present_strict",
                                   new=mock.AsyncMock(return_value=False)), \
                 mock.patch.object(model_lifecycle._ms, "_START_PROBE_DELAY", 0), \
                 mock.patch.object(model_lifecycle._ms, "_watchdog_on_crash") as crash:
                sm.get_model.return_value = self._fake_model(ModelStatus.LOADING)
                model_lifecycle._stopping_models.add("m1")
                try:
                    await model_lifecycle._verify_start_probe("m1", "10.0.0.7", 8000)
                finally:
                    model_lifecycle._stopping_models.discard("m1")
                sm.update_model_status.assert_not_called()
                sm.clear_current_model.assert_not_called()
                crash.assert_not_called()
        asyncio.run(scenario())

    def test_probe_still_rolls_back_without_stop_flag(self):
        async def scenario():
            with mock.patch.object(model_lifecycle._ms, "state_machine") as sm, \
                 mock.patch.object(model_lifecycle._ms, "_check_vllm_present_strict",
                                   new=mock.AsyncMock(return_value=False)), \
                 mock.patch.object(model_lifecycle._ms, "_START_PROBE_DELAY", 0), \
                 mock.patch.object(model_lifecycle._ms, "_watchdog_on_crash") as crash:
                sm.get_model.return_value = self._fake_model(ModelStatus.LOADING)
                await model_lifecycle._verify_start_probe("m1", "10.0.0.7", 8000)
                sm.update_model_status.assert_called_once_with("m1", ModelStatus.STOPPED)
                sm.clear_current_model.assert_called_once_with("m1")
                crash.assert_called_once_with("m1")
        asyncio.run(scenario())

    def test_stop_model_flags_model_for_whole_stop(self):
        async def scenario():
            seen = {}

            def fake_stop_vllm(force, model_path):
                seen["flag_during"] = "m1" in model_lifecycle._stopping_models
                return {"success": True, "mode": "scoped", "error": None}

            executor = mock.MagicMock()
            executor.stop_vllm = fake_stop_vllm

            async def fake_wait(executor, max_wait=8, model_path=""):
                return True

            model = mock.MagicMock()
            model.path = "/models/m1"
            with mock.patch.object(model_lifecycle._ms, "get_remote_executor", return_value=executor), \
                 mock.patch.object(model_lifecycle._ms, "state_machine") as sm, \
                 mock.patch.object(model_lifecycle, "_wait_for_remote_stop", new=fake_wait), \
                 mock.patch.object(model_lifecycle, "_unset_launch_env_vars", new=mock.AsyncMock()):
                sm.get_model.return_value = model
                result = await model_lifecycle.stop_model("m1")
            self.assertTrue(seen["flag_during"])
            self.assertNotIn("m1", model_lifecycle._stopping_models)
            self.assertTrue(result["stopped"])
        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# SEC-1: PUT /deployment/state field caps
# ---------------------------------------------------------------------------

class TestPutStateFieldCaps(unittest.TestCase):
    @staticmethod
    def _run(body):
        with mock.patch.object(ds, "deployment_state") as state:
            state.get.return_value = {}
            return asyncio.run(put_state(body)), state

    def test_selected_str_over_cap_rejected(self):
        # the exception is raised before any update_section call
        with mock.patch.object(ds, "deployment_state") as state:
            state.get.return_value = {}
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(put_state({"selected": {"driver": "x" * 600}}))
        self.assertEqual(ctx.exception.status_code, 400)
        state.update_section.assert_not_called()

    def test_mirror_over_cap_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run({"mirrors": {"pypi": "https://" + "a" * 400}})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_normal_values_still_accepted(self):
        _, state = self._run({
            "mirrors": {"pypi": "https://mirrors.aliyun.com/pypi/simple/"},
            "selected": {"driver": "nvidia-driver-580", "cuda_install_system": True},
        })
        state.update_section.assert_any_call(
            "mirrors", pypi="https://mirrors.aliyun.com/pypi/simple/")
        state.update_section.assert_any_call(
            "selected", driver="nvidia-driver-580", cuda_install_system=True)


# ---------------------------------------------------------------------------
# INF-3: /health readiness probe
# ---------------------------------------------------------------------------

class TestHealthReadinessProbe(unittest.TestCase):
    def test_missing_data_dir_reports_degraded(self):
        with mock.patch.object(main_mod, "DATA_DIR", "/nonexistent/vdb_batch2_test"):
            resp = TestClient(main_mod.app).get("/health")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "degraded")
        self.assertIn("data dir missing or not writable", body["problems"])

    def test_missing_static_index_reports_degraded(self):
        static_tmp = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(main_mod, "_STATIC_DIR", static_tmp.name), \
                 mock.patch.dict(os.environ, {"STATIC_DIR": static_tmp.name}):
                resp = TestClient(main_mod.app).get("/health")
            self.assertEqual(resp.status_code, 503)
            self.assertIn("static index.html missing", resp.json()["problems"])
        finally:
            static_tmp.cleanup()

    def test_ready_container_reports_healthy(self):
        static_tmp = tempfile.TemporaryDirectory()
        data_tmp = tempfile.TemporaryDirectory()
        try:
            with open(os.path.join(static_tmp.name, "index.html"), "w", encoding="utf-8") as f:
                f.write("<html></html>")
            with mock.patch.object(main_mod, "_STATIC_DIR", static_tmp.name), \
                 mock.patch.dict(os.environ, {"STATIC_DIR": static_tmp.name}), \
                 mock.patch.object(main_mod, "DATA_DIR", data_tmp.name):
                resp = TestClient(main_mod.app).get("/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "healthy")
        finally:
            static_tmp.cleanup()
            data_tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
