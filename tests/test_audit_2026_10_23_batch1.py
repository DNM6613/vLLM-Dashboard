"""Audit 2026-10-23 batch 1 fixes: E-1 (deploy script: a failed docker run
or an SSH break AFTER the old container is removed must roll the previous
image back), B-1 (deployment stop window: the pipeline's kill of vLLM must
not be read by the status sync as a model crash), B-2 (a vLLM install and a
vLLM rollback share one mutual-exclusion domain)."""
import asyncio
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

from backend.controller.state_machine import state_machine
from backend.schemas.model import ModelStatus
from backend.service import (
    deployment_common as dcommon,
)
from backend.service import (
    deployment_entry,
    model_service,
    model_watchdog,
)
from backend.service import (
    deployment_service as ds,
)
from tools import server_deploy as sd

# model_lifecycle / model_status_sync must be imported only after the facade
# is fully loaded: they bind the facade on import, and importing one first
# re-enters the still-initializing facade (circular from-import).
from backend.service import model_lifecycle, model_status_sync  # isort: skip


def _reset_watchdog():
    model_watchdog._crash_events.clear()
    model_watchdog._suspended.clear()
    model_watchdog._inflight.clear()


# ---------------------------------------------------------------------------
# B-2: vllm x rollback_vllm mutual exclusion
# ---------------------------------------------------------------------------

class TestVllmRollbackMutexDomain(unittest.TestCase):
    """audit 2026-10-23 B-2: a vLLM install and a vLLM rollback both
    rewrite the same venv — they must refuse each other (the REM-5
    driver/cuda pairing, same technique)."""

    @staticmethod
    def _inflight(kinds_hit):
        def fake_inflight(kind):
            if kind in kinds_hit:
                t = mock.MagicMock()
                t.kind = kind
                return t
            return None
        return fake_inflight

    def test_rollback_refused_when_vllm_inflight(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"vllm"})), \
                self.assertRaises(ds.DeploymentError) as ctx:
            deployment_entry._reject_inflight("rollback_vllm")
        self.assertIn("vLLM", str(ctx.exception))

    def test_vllm_refused_when_rollback_inflight(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"rollback_vllm"})), \
                self.assertRaises(ds.DeploymentError) as ctx:
            deployment_entry._reject_inflight("vllm")
        self.assertIn("vLLM roll back", str(ctx.exception))

    def test_vllm_ignores_driver_and_cuda(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"driver", "cuda"})):
            deployment_entry._reject_inflight("vllm")  # no raise

    def test_domain_table(self):
        self.assertEqual(dcommon.inflight_domain("vllm"), ("vllm", "rollback_vllm"))
        self.assertEqual(dcommon.inflight_domain("rollback_vllm"),
                         ("vllm", "rollback_vllm"))
        self.assertEqual(dcommon.inflight_domain("driver"), ("driver", "cuda"))
        self.assertEqual(dcommon.inflight_domain("cuda"), ("driver", "cuda"))
        self.assertEqual(dcommon.inflight_domain("reboot"), ("reboot",))


class TestCreateVllmRollbackDomain(unittest.TestCase):
    """the create-layer atomic claim (BCK-3) must span the whole domain —
    the entry precheck alone is a TOCTOU window."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._files = mock.patch.multiple(
            ds,
            STATE_FILE=os.path.join(self.tmp.name, "state.json"),
            TASKS_FILE=os.path.join(self.tmp.name, "tasks.json"),
            DATA_DIR=self.tmp.name,
            LOG_DIR=os.path.join(self.tmp.name, "logs"),
        )
        self._files.start()
        self.mgr = ds.DeploymentTaskManager()

    def tearDown(self):
        self._files.stop()
        self.tmp.cleanup()

    def test_create_rollback_refused_while_vllm_inflight(self):
        first = self.mgr.create("vllm", "install", ["s1"])
        first.status = "running"
        with self.assertRaises(ds.DeploymentError) as cm:
            self.mgr.create("rollback_vllm", "rollback", ["s1"])
        self.assertIn("vllm", str(cm.exception))

    def test_create_vllm_refused_while_rollback_inflight(self):
        first = self.mgr.create("rollback_vllm", "rollback", ["s1"])
        first.status = "running"
        with self.assertRaises(ds.DeploymentError) as cm:
            self.mgr.create("vllm", "install", ["s1"])
        self.assertIn("rollback_vllm", str(cm.exception))


# ---------------------------------------------------------------------------
# B-1: deployment stop window
# ---------------------------------------------------------------------------

class TestDeploymentStopWindow(unittest.TestCase):
    """audit 2026-10-23 B-1: begin_deployment_stop_window flips every
    RUNNING/LOADING model to STOPPED inside the stop window and drops the
    crash history, so the status sync attributes the pipeline's kill to
    the deployment instead of recording a crash."""

    def setUp(self):
        _reset_watchdog()
        model_lifecycle._stopping_models.clear()
        self._sm = state_machine
        self._ids = [
            self._sm.add_model(n, n, f"/models/{n}",
                               config={"name": n, "path": f"/models/{n}"}).id
            for n in ("b1-a", "b1-b", "b1-c")
        ]

    def tearDown(self):
        model_lifecycle._stopping_models.clear()
        _reset_watchdog()
        for mid in self._ids:
            self._sm.remove_model(mid)

    def _remote_dead(self):
        """Remote vLLM API unreachable + no process."""
        class _DeadConfig:
            host = "10.131.1.7"

        class _DeadConfigManager:
            def get_config(self):
                return _DeadConfig()

        class _DeadTarget:
            base_url = "http://10.131.1.7:8000"
            port = 8000
            headers = {}

        class _DeadClient:
            async def get(self, *args, **kwargs):
                raise RuntimeError("connection refused")

        async def gone_present(host, port):
            return False

        self._saved = (
            model_service.config_manager,
            model_service.get_http_client,
            model_service.resolve_api_target,
            model_status_sync._check_vllm_present_strict,
        )
        model_service.config_manager = _DeadConfigManager()
        model_service.get_http_client = lambda: _DeadClient()
        model_service.resolve_api_target = lambda config: _DeadTarget()
        model_status_sync._check_vllm_present_strict = gone_present

    def _restore_remote(self):
        (model_service.config_manager, model_service.get_http_client,
         model_service.resolve_api_target,
         model_status_sync._check_vllm_present_strict) = self._saved

    def test_begin_flips_running_and_loading_only(self):
        a, b, c = self._ids
        self._sm.set_current_model(a)
        self._sm.update_model_status(a, ModelStatus.RUNNING)
        self._sm.set_model_locked(a, True)
        model_watchdog._crash_events[a] = [(datetime.now(), False)]
        self._sm.update_model_status(b, ModelStatus.LOADING)
        # c stays at its default STOPPED — must not be touched
        affected = model_lifecycle.begin_deployment_stop_window()
        try:
            self.assertEqual(sorted(affected), sorted((a, b)))
            self.assertEqual(self._sm.get_model(a).status, ModelStatus.STOPPED)
            self.assertEqual(self._sm.get_model(b).status, ModelStatus.STOPPED)
            self.assertTrue(model_lifecycle.is_stopping(a))
            self.assertTrue(model_lifecycle.is_stopping(b))
            self.assertFalse(model_lifecycle.is_stopping(c))
            self.assertNotIn(a, model_watchdog._crash_events)
            self.assertIsNone(self._sm.current_model_id)
        finally:
            model_lifecycle.end_deployment_stop_window(affected)
        self.assertFalse(model_lifecycle.is_stopping(a))
        self.assertFalse(model_lifecycle.is_stopping(b))
        self.assertEqual(self._sm.get_model(c).status, ModelStatus.STOPPED)

    def test_sync_records_no_crash_during_window(self):
        # The B-1 regression: after the pipeline's kill, a sync tick must
        # not record a crash for the model the deployment stopped.
        mid = self._ids[0]
        # control: the same transition WITHOUT the window IS a crash
        self._sm.update_model_status(mid, ModelStatus.RUNNING)
        self._sm.set_model_locked(mid, True)
        self._remote_dead()
        asyncio.run(model_status_sync._sync_status_once(timeout=3.0))
        self.assertEqual(self._sm.get_model(mid).status, ModelStatus.STOPPED)
        self.assertIn(mid, model_watchdog._crash_events)
        _reset_watchdog()
        # window: the pipeline's stop is attributed to the deployment
        self._sm.update_model_status(mid, ModelStatus.RUNNING)
        affected = model_lifecycle.begin_deployment_stop_window()
        try:
            asyncio.run(model_status_sync._sync_status_once(timeout=3.0))
        finally:
            self._restore_remote()
            model_lifecycle.end_deployment_stop_window(affected)
        self.assertEqual(self._sm.get_model(mid).status, ModelStatus.STOPPED)
        self.assertNotIn(mid, model_watchdog._crash_events)


class TestSubmitRunsPipelineInStopWindow(unittest.TestCase):
    """audit 2026-10-23 B-1: submit wraps driver/vllm/rollback_vllm
    pipelines in the deployment stop window for the whole run (including
    the failure path); the CUDA pipeline (no stop-vLLM step) gets none."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._files = mock.patch.multiple(
            ds,
            STATE_FILE=os.path.join(self.tmp.name, "state.json"),
            TASKS_FILE=os.path.join(self.tmp.name, "tasks.json"),
            DATA_DIR=self.tmp.name,
            LOG_DIR=os.path.join(self.tmp.name, "logs"),
        )
        self._files.start()
        _reset_watchdog()
        self._sm = state_machine
        self._mid = self._sm.add_model(
            "b1-submit", "b1-submit", "/models/b1-submit",
            config={"name": "b1-submit", "path": "/models/b1-submit"},
        ).id
        self._sm.set_model_locked(self._mid, True)
        self._sm.update_model_status(self._mid, ModelStatus.RUNNING)
        self.mgr = ds.DeploymentTaskManager()
        self._exec = mock.patch.object(
            ds, "get_remote_executor", return_value=mock.MagicMock())
        self._exec.start()

    def tearDown(self):
        self._exec.stop()
        self._files.stop()
        self.tmp.cleanup()
        model_lifecycle._stopping_models.discard(self._mid)
        _reset_watchdog()
        self._sm.remove_model(self._mid)

    def test_window_open_during_runner_closed_after_success(self):
        seen = {}

        def runner(ctx):
            seen["stopping"] = model_lifecycle.is_stopping(self._mid)
            seen["status"] = self._sm.get_model(self._mid).status

        task = self.mgr.create("vllm", "install", ["s"])
        self.mgr.submit(task, runner)
        self.assertTrue(self.mgr.wait_terminal(task.id, timeout=10))
        self.assertEqual(task.status, "success")
        self.assertTrue(seen["stopping"])
        self.assertEqual(seen["status"], ModelStatus.STOPPED)
        self.assertFalse(model_lifecycle.is_stopping(self._mid))
        self.assertNotIn(self._mid, model_watchdog._crash_events)

    def test_window_closed_when_pipeline_fails(self):
        def runner(ctx):
            raise ds.DeploymentError("boom")

        task = self.mgr.create("rollback_vllm", "rollback", ["s"])
        self.mgr.submit(task, runner)
        self.assertTrue(self.mgr.wait_terminal(task.id, timeout=10))
        self.assertEqual(task.status, "failed")
        self.assertFalse(model_lifecycle.is_stopping(self._mid))
        self.assertEqual(self._sm.get_model(self._mid).status, ModelStatus.STOPPED)

    def test_window_logged_in_task_log(self):
        def runner(ctx):
            pass

        task = self.mgr.create("vllm", "install", ["s"])
        self.mgr.submit(task, runner)
        self.assertTrue(self.mgr.wait_terminal(task.id, timeout=10))
        log = "\n".join(self.mgr.file_tail(task.id, 50))
        self.assertIn("Stop window", log)
        self.assertIn(self._mid, log)

    def test_driver_kind_in_window(self):
        seen = {}

        def runner(ctx):
            seen["stopping"] = model_lifecycle.is_stopping(self._mid)

        task = self.mgr.create("driver", "driver", ["s"])
        self.mgr.submit(task, runner)
        self.assertTrue(self.mgr.wait_terminal(task.id, timeout=10))
        self.assertTrue(seen.get("stopping"))

    def test_cuda_kind_out_of_window(self):
        seen = {}

        def runner(ctx):
            seen["stopping"] = model_lifecycle.is_stopping(self._mid)

        task = self.mgr.create("cuda", "cuda", ["s"])
        self.mgr.submit(task, runner)
        self.assertTrue(self.mgr.wait_terminal(task.id, timeout=10))
        self.assertFalse(seen.get("stopping"))
        # the model was NOT flipped — the CUDA pipeline never stopped it
        self.assertEqual(self._sm.get_model(self._mid).status, ModelStatus.RUNNING)


# ---------------------------------------------------------------------------
# E-1: deploy script run-failure / SSH-break rollback
# ---------------------------------------------------------------------------

class TestServerDeployRunFailureRollback(unittest.TestCase):
    """audit 2026-10-23 E-1: after the old container is removed, a failed
    docker run (rc != 0), an SSH break mid-run, or a broken health gate
    must roll the previous image back instead of leaving the port dark."""

    def _fake_remote(self, run_rc=1, run_raises=False):
        """Simulate the remote command sequence of a deployment."""
        calls = []

        def fake_remote(c, cmd, timeout=120):
            calls.append(cmd)
            if cmd.startswith("docker inspect"):
                return 0, "vllm-dashboard:OLD\n", ""
            if "docker build" in cmd or cmd.startswith("rm -rf") or "mkdir -p" in cmd:
                return 0, "built\n", ""
            if "docker stop" in cmd or "docker ps -a --filter" in cmd:
                return 0, "", ""
            if "docker run" in cmd:
                if "vllm-dashboard:NEW" in cmd:
                    if run_raises:
                        raise RuntimeError("SSH connection lost")
                    return run_rc, "run out", "run err"
                # the rollback run restarts the previous image
                return 0, "rolled-back-container-id", ""
            if "wget" in cmd:
                return 0, "status: healthy", ""
            return 0, "", ""

        return fake_remote, calls

    def _run_deploy(self, fake, image="vllm-dashboard:NEW"):
        c = mock.MagicMock()
        buf = io.StringIO()
        # getsize is patched too: _run_deploy prints the tar size straight
        # from the (patched, never created) file — the test must not depend
        # on a leftover _server_deploy.tar in the repo root.
        with mock.patch.object(sd, "remote", fake), \
                mock.patch.object(sd, "build_tar"), \
                mock.patch.object(sd.os.path, "getsize", return_value=290688), \
                mock.patch.object(sd.time, "sleep"), \
                redirect_stdout(buf):
            sd._run_deploy(c, image)
        return buf.getvalue()

    def test_run_failure_rolls_back_previous_image(self):
        fake, calls = self._fake_remote(run_rc=1)
        text = self._run_deploy(fake)
        self.assertIn("ROLLED_BACK vllm-dashboard:OLD", text)
        run_old = [c for c in calls
                   if "docker run" in c and "vllm-dashboard:OLD" in c]
        self.assertEqual(len(run_old), 1)

    def test_ssh_break_mid_run_rolls_back(self):
        fake, calls = self._fake_remote(run_raises=True)
        text = self._run_deploy(fake)
        self.assertIn("RUN INTERRUPTED", text)
        self.assertIn("ROLLED_BACK vllm-dashboard:OLD", text)
        run_old = [c for c in calls
                   if "docker run" in c and "vllm-dashboard:OLD" in c]
        self.assertEqual(len(run_old), 1)

    def test_healthy_new_image_does_not_roll_back(self):
        # control: a successful run + a green gate deploys without touching
        # the previous image
        fake, calls = self._fake_remote(run_rc=0)
        text = self._run_deploy(fake)
        self.assertIn("DEPLOY_OK vllm-dashboard:NEW", text)
        run_old = [c for c in calls
                   if "docker run" in c and "vllm-dashboard:OLD" in c]
        self.assertEqual(run_old, [])

    def test_run_failure_without_previous_image_exits(self):
        def fake_remote(c, cmd, timeout=120):
            if cmd.startswith("docker inspect"):
                return 0, "\n", ""
            if "docker run" in cmd:
                return 1, "", ""
            return 0, "", ""

        c = mock.MagicMock()
        buf = io.StringIO()
        with mock.patch.object(sd, "remote", fake_remote), \
                mock.patch.object(sd, "build_tar"), \
                mock.patch.object(sd.os.path, "getsize", return_value=290688), \
                mock.patch.object(sd.time, "sleep"), \
                redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            sd._run_deploy(c, "vllm-dashboard:NEW")
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("ROLLBACK_IMPOSSIBLE", buf.getvalue())
