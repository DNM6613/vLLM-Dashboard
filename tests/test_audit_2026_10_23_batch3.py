"""Audit 2026-10-23 batch 3 fixes: B-3 (source build cleans the fixed
clone dir and pins the selected version's tag instead of silently
installing main HEAD), B-5 (a reboot is refused while any pipeline is in
flight — one-way, a reboot never blocks a pipeline), B-6 (a failed driver
install after the purge reinstalls the previous driver instead of leaving
the machine without one)."""
import contextlib
import os
import tempfile
import unittest
from unittest import mock

from backend.service import (
    deployment_common as dcommon,
)
from backend.service import (
    deployment_entry,
)
from backend.service import (
    deployment_pipelines_env as dp_env,
)
from backend.service import (
    deployment_pipelines_vllm as dp_vllm,
)
from backend.service import (
    deployment_service as ds,
)
from backend.service.deployment_common import DeploymentError

# ---------------------------------------------------------------------------
# B-3: source build — clean dir + version tag
# ---------------------------------------------------------------------------

class TestSourceBuildVersionAndClean(unittest.TestCase):
    """audit 2026-10-23 B-3: the source-build branch used to clone main
    HEAD no matter which version was selected, and the fixed clone dir
    was never cleaned — a second run died on 'already exists'."""

    def test_selected_version_pinned_to_tag(self):
        _, install = dp_vllm._build_vllm_install_commands(
            {"env_mode": "existing", "source_build": True,
             "version": "0.32.0"}, "/venv", {})
        self.assertIn(
            "rm -rf /tmp/vllm-src-vdb && git clone --depth 1 --branch v0.32.0",
            install)
        self.assertIn("uv pip install", install)

    def test_latest_clones_head_without_branch(self):
        _, install = dp_vllm._build_vllm_install_commands(
            {"env_mode": "existing", "source_build": True,
             "version": "latest"}, "/venv", {})
        self.assertIn("rm -rf /tmp/vllm-src-vdb", install)
        self.assertNotIn("--branch", install)

    def test_non_source_branch_untouched(self):
        _, install = dp_vllm._build_vllm_install_commands(
            {"env_mode": "existing", "version": "0.32.0"}, "/venv", {})
        self.assertNotIn("git clone", install)
        self.assertNotIn("rm -rf /tmp/vllm-src-vdb", install)
        self.assertIn("vllm==0.32.0", install)


# ---------------------------------------------------------------------------
# B-5: reboot refused while a pipeline is in flight
# ---------------------------------------------------------------------------

class TestRebootRefusedWhilePipelineInflight(unittest.TestCase):
    """audit 2026-10-23 B-5: a reboot mid apt/uv install kills the nohup
    job and leaves dpkg half-configured — refuse it while any pipeline
    (driver/cuda/vllm/rollback_vllm) is in flight; one-way (a reboot
    never blocks a pipeline)."""

    @staticmethod
    def _inflight(kinds_hit):
        def fake_inflight(kind):
            if kind in kinds_hit:
                t = mock.MagicMock()
                t.kind = kind
                return t
            return None
        return fake_inflight

    def test_domain_table_one_way(self):
        self.assertEqual(dcommon.inflight_domain("reboot"),
                         ("driver", "cuda", "vllm", "rollback_vllm"))
        self.assertNotIn("reboot", dcommon.inflight_domain("driver"))
        self.assertNotIn("reboot", dcommon.inflight_domain("vllm"))

    def test_refused_when_vllm_inflight(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"vllm"})), \
                self.assertRaises(ds.DeploymentError) as cm:
            deployment_entry._reject_inflight("reboot")
        self.assertIn("vLLM", str(cm.exception))

    def test_refused_when_driver_inflight(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight({"driver"})), \
                self.assertRaises(ds.DeploymentError) as cm:
            deployment_entry._reject_inflight("reboot")
        self.assertIn("Driver", str(cm.exception))

    def test_allowed_when_nothing_inflight(self):
        with mock.patch.object(deployment_entry._ds.task_manager, "inflight_task",
                               side_effect=self._inflight(set())):
            deployment_entry._reject_inflight("reboot")  # no raise


class TestRebootCreateLayer(unittest.TestCase):
    """the create-layer atomic claim (BCK-3 / B-2 pattern) spans the same
    domain — the entry precheck alone is a TOCTOU window."""

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

    def test_create_reboot_refused_while_cuda_inflight(self):
        first = self.mgr.create("cuda", "install", ["s1"])
        first.status = "running"
        with self.assertRaises(ds.DeploymentError) as cm:
            self.mgr.create("reboot", "reboot", ["s1"])
        self.assertIn("cuda", str(cm.exception))

    def test_pipeline_ignores_reboot(self):
        # one-way: an in-flight reboot does not block a pipeline
        first = self.mgr.create("reboot", "reboot", ["s1"])
        first.status = "running"
        self.mgr.create("cuda", "install", ["s1"])  # no raise


# ---------------------------------------------------------------------------
# B-6: failed driver install after the purge reinstalls the previous one
# ---------------------------------------------------------------------------

class _FakeDriverCtx:
    """Bare TaskContext stub: no-op steps, recorded run_long calls."""

    def __init__(self, effects):
        self.task = mock.MagicMock()
        self.task.steps = [{"name": f"s{i}", "status": "pending"}
                           for i in range(4)]
        self.manager = mock.MagicMock()
        self.executor = mock.MagicMock()
        self.executor.stop_vllm.return_value = {"success": True}
        self._effects = list(effects)
        self.calls: list[str] = []
        self.logs: list[str] = []

    def log(self, message):
        self.logs.append(str(message))

    def step(self, index):
        return contextlib.nullcontext()

    def run_long(self, command, timeout=1800):
        self.calls.append(command)
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return {"success": True}


class _FakeDriverState:
    def __init__(self, previous_pkg):
        self._previous = previous_pkg
        self.snapshots: list[tuple[str, dict]] = []

    def get(self):
        return {"snapshots": {"driver": {"previous_pkg": self._previous}}}

    def set_snapshot(self, section, **kwargs):
        self.snapshots.append((section, kwargs))


class TestDriverInstallSelfHeal(unittest.TestCase):
    """audit 2026-10-23 B-6: step 1 purges every nvidia-* package, so a
    failed target install must reinstall the previously recorded driver —
    the task still fails with the original error."""

    PREVIOUS = "nvidia-driver-580"
    TARGET = "nvidia-driver-595"

    def _run(self, effects, previous_pkg):
        ctx = _FakeDriverCtx(effects)
        state = _FakeDriverState(previous_pkg)
        with mock.patch.object(dp_env._ds, "deployment_state", state):
            error: DeploymentError | None = None
            try:
                dp_env._run_driver(ctx, self.TARGET, 595)
            except DeploymentError as e:
                error = e
        return ctx, state, error

    def test_failure_reinstalls_previous_driver(self):
        ctx, state, error = self._run(
            [{}, DeploymentError("install failed"), {}], self.PREVIOUS)
        self.assertIsNotNone(error)
        self.assertEqual(len(ctx.calls), 3)
        self.assertIn(self.PREVIOUS, ctx.calls[2])
        self.assertTrue(any("Self-heal: reinstalled" in line
                            for line in ctx.logs))
        # the failed install must not record the target as current
        self.assertEqual(state.snapshots, [])

    def test_self_heal_failure_shows_manual_command(self):
        ctx, _, error = self._run(
            [{}, DeploymentError("install failed"),
             DeploymentError("heal failed")], self.PREVIOUS)
        self.assertIsNotNone(error)
        self.assertEqual(len(ctx.calls), 3)
        manual = [line for line in ctx.logs if "Manual recovery" in line]
        self.assertTrue(manual)
        self.assertIn(self.PREVIOUS, manual[0])

    def test_failure_without_previous_logs_manual_command_only(self):
        ctx, _, error = self._run(
            [{}, DeploymentError("install failed")], "")
        self.assertIsNotNone(error)
        self.assertEqual(len(ctx.calls), 2)  # no self-heal attempt
        self.assertTrue(any("Manual recovery" in line for line in ctx.logs))

    def test_success_never_self_heals(self):
        ctx, state, error = self._run([{}, {}], self.PREVIOUS)
        self.assertIsNone(error)
        self.assertEqual(len(ctx.calls), 2)
        self.assertEqual(state.snapshots,
                         [("driver", {"current_pkg": self.TARGET})])
