import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.api import deployment as api_dep
from backend.executor.remote import deployment_ops as ops
from backend.service import deployment_service as ds

SAMPLE_DRIVERS_RAW = """
ERROR:root:could not get a device
== /sys/devices/pci0000:65/0000:65:00.0/nvme ==
  modalias : pci:v000010DEd00002685sv000010DE
  vendor   : 0x10de "NVIDIA Corporation"
  model    : NVIDIA GeForce RTX 5090
  driver   : nvidia-driver-570-server - distro supported
  driver   : nvidia-driver-580 - distro supported
  driver   : nvidia-driver-570-open - distro supported
  driver   : nvidia-driver-580-open - distro supported
== /sys/devices/pci0000:66/0000:66:00.0/nvme ==
  modalias : pci:v000010DEd00002685sv000010DE
  vendor   : 0x10de "NVIDIA Corporation"
  model    : NVIDIA GeForce RTX 5090
  driver   : nvidia-driver-580 - distro supported
  driver   : nvidia-driver-580-open - distro supported
  driver   : nvidia-driver-565 - distro third-party
  driver   : nouveau - xfree86-v4-3.0.x
"""


class TestParseUbuntuDrivers(unittest.TestCase):
    def test_parses_models_and_dedupes_across_gpus(self):
        result = ops.parse_ubuntu_drivers(SAMPLE_DRIVERS_RAW)
        self.assertEqual(result["gpu_models"], ["NVIDIA GeForce RTX 5090"])
        packages = [r["package"] for r in result["drivers"]]
        # nvidia-driver-580 / -open are listed per GPU but deduped to one row
        self.assertEqual(packages, [
            "nvidia-driver-570-server",
            "nvidia-driver-580",
            "nvidia-driver-570-open",
            "nvidia-driver-580-open",
            "nvidia-driver-565",
            "nouveau",
        ])

    def test_versions_tags_and_flags(self):
        rows = {r["package"]: r for r in ops.parse_ubuntu_drivers(SAMPLE_DRIVERS_RAW)["drivers"]}
        server = rows["nvidia-driver-570-server"]
        self.assertEqual(server["version"], 570)
        tag_keys = [t["key"] for t in server["tags"]]
        self.assertIn("version", tag_keys)
        self.assertIn("server", tag_keys)
        self.assertIn("distro", tag_keys)
        self.assertFalse(server["nouveau"])

        open_row = rows["nvidia-driver-580-open"]
        self.assertIn("open", [t["key"] for t in open_row["tags"]])
        self.assertEqual(open_row["attrs"], ["distro"])

        legacy = rows["nvidia-driver-565"]
        self.assertEqual(legacy["attrs"], ["distro", "third-party"])
        self.assertFalse(legacy["recommended"])

        nouveau = rows["nouveau"]
        self.assertIsNone(nouveau["version"])
        self.assertTrue(nouveau["nouveau"])

    def test_recommended_flag_from_attrs(self):
        raw = "  driver   : nvidia-driver-580 - distro recommended\n"
        rows = ops.parse_ubuntu_drivers(raw)["drivers"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["recommended"])
        self.assertEqual(rows[0]["version"], 580)


# Live capture from the AI server (2026-09-17): `modalias` / `vendor` lines
# are NOT indented on this host, `nvidia-driver-open` is a versionless meta
# entry. In the dpkg section `nvidia-driver-595` has status `un` (never
# installed — dpkg-query -W still lists it) while only
# `nvidia-driver-595-open` is genuinely installed (ii).
LIVE_DRIVER_PROBE_OUTPUT = """== /sys/devices/pci0000:c0/0000:c0:01.1/0000:c1:00.0 ==
modalias : pci:v000010DEd00002D04sv00001043sd00008A11bc03sc00i00
vendor   : NVIDIA Corporation
model    : GB206 [GeForce RTX 5060 Ti]
driver   : nvidia-driver-610 - distro non-free
driver   : nvidia-driver-595 - distro non-free
driver   : nvidia-driver-595-open - distro non-free recommended
driver   : nvidia-driver-595-server - distro non-free
driver   : nvidia-driver-595-server-open - distro non-free
driver   : nvidia-driver-open - third-party non-free
driver   : xserver-xorg-video-nouveau - distro free builtin
---VDB_SEP---
595.91.07
---VDB_SEP2---
nvidia-driver-595\tunknown ok not-installed
nvidia-driver-595-open\tinstall ok installed
nvidia-driver-binary\tunknown ok not-installed
DONE
"""


class _FakeExecutor:
    def __init__(self, stdout: str):
        self._stdout = stdout

    def execute(self, command, timeout=None, stdin_data=None):
        return {"success": True, "stdout": self._stdout, "stderr": "", "returncode": 0}


class TestGetDriverList(unittest.TestCase):
    def _probe(self, stdout: str):
        import types

        return types.MethodType(
            ops.DeploymentOps.get_driver_list, _FakeExecutor(stdout))()

    def test_parses_live_probe_output(self):
        res = self._probe(LIVE_DRIVER_PROBE_OUTPUT)
        self.assertTrue(res["success"])
        self.assertEqual(res["current_driver"], "595.91.07")
        # only the genuinely installed (ii) variant survives: the bare
        # nvidia-driver-595 has status `un` (never installed, yet listed
        # by dpkg-query -W), and nvidia-driver-binary is not a row in the
        # ubuntu-drivers list
        self.assertEqual(res["installed_packages"], ["nvidia-driver-595-open"])
        self.assertEqual(res["gpu_models"], ["GB206 [GeForce RTX 5060 Ti]"])
        rows = {r["package"]: r for r in res["drivers"]}
        self.assertEqual(rows["nvidia-driver-595"]["version"], 595)
        self.assertTrue(rows["nvidia-driver-595-open"]["recommended"])
        self.assertIsNone(rows["nvidia-driver-open"]["version"])
        self.assertTrue(rows["xserver-xorg-video-nouveau"]["nouveau"])

    def test_missing_dpkg_section_yields_empty_packages(self):
        stdout = (
            "driver   : nvidia-driver-580 - distro supported\n"
            "---VDB_SEP---\n580.65.06\nDONE\n")
        res = self._probe(stdout)
        self.assertEqual(res["current_driver"], "580.65.06")
        self.assertEqual(res["installed_packages"], [])


class TestYamlRoundTrip(unittest.TestCase):
    def _round_trip(self, inner: dict):
        doc = {"vllm_dashboard_env": inner}
        text = "\n".join(api_dep._yaml_dump(doc)) + "\n"
        return api_dep.parse_env_yaml(text)

    def test_export_shape_round_trips(self):
        inner = {
            "exported_at": "2026-09-17 10:00:00",
            "driver": {
                "current_driver": "570.86.15",
                "current_pkg": "nvidia-driver-570-server",
                "previous_pkg": "",
            },
            "cuda": {
                "selected_version": "12.9",
                "install_system": False,
                "installed_version": "",
            },
            "vllm": {
                "version": "0.29.0",
                "runtime": "builtin",
                "venv": ".vllm",
            },
            "mirrors": {
                "pypi": "https://pypi.tuna.tsinghua.edu.cn/simple/",
                "hf": "https://hf-mirror.com",
            },
        }
        self.assertEqual(self._round_trip(inner), inner)

    def test_string_with_quotes_and_backslash(self):
        inner = {"s": 'a "b" c\\d'}
        self.assertEqual(self._round_trip(inner)["s"], 'a "b" c\\d')

    def test_bool_and_plain_token_scalars(self):
        inner = {"flag": True, "count": "3"}
        parsed = self._round_trip(inner)
        self.assertIs(parsed["flag"], True)
        # plain (unquoted) tokens parse back as strings — version values like
        # "12.90" must keep their exact spelling, so the parser never coerces
        # to a number
        self.assertEqual(parsed["count"], "3")

    def test_unsupported_line_raises(self):
        with self.assertRaises(ValueError):
            api_dep.parse_env_yaml("- list item\n")


class TestValidationHelpers(unittest.TestCase):
    def test_venv_name_validation(self):
        for valid in (".vllm", "~/envs/vllm", "/opt/vllm", "my-env_1"):
            ds._validate_venv_name(valid)  # must not raise
        for invalid in ("", "a b", "a" * 129, "env;rm", "../x", "a/../b"):
            with self.assertRaises(ds.DeploymentError, msg=invalid):
                ds._validate_venv_name(invalid)

    def test_resolve_venv_path(self):
        self.assertEqual(ds._resolve_venv_path(".vllm"), "$HOME/.vllm")
        self.assertEqual(ds._resolve_venv_path("~/.vllm"), "$HOME/.vllm")
        self.assertEqual(ds._resolve_venv_path("~"), "$HOME")
        self.assertEqual(ds._resolve_venv_path("/abs/path"), "/abs/path")

    def test_major_driver_version(self):
        self.assertEqual(ds._major_driver_version("570.86.15"), 570)
        self.assertIsNone(ds._major_driver_version(""))
        self.assertIsNone(ds._major_driver_version("garbage"))

    def test_suggest_error(self):
        self.assertIn("mirror", ds._suggest_error("curl: (28) timed out").lower())
        self.assertIn("apt/dpkg", ds._suggest_error("Could not get lock: /var/lib/dpkg/lock"))
        self.assertIn("disk", ds._suggest_error("No space left on device"))
        self.assertIn("sudo", ds._suggest_error("sudo: a password is required"))
        self.assertEqual(ds._suggest_error("something else entirely"), "")

    def test_compat_tables(self):
        # NVIDIA CUDA Toolkit release notes, sub-version compatibility table
        # (Linux x86_64): the whole 13.x series requires driver >= 580 (no
        # maximum driver). vLLM's default wheel is built with CUDA 12.9
        # (min driver 575), so VLLM_MIN_DRIVER stays 575. DRIVER_MIN_CUDA
        # stays empty: drivers are backward compatible with older toolkits.
        self.assertEqual(ds.CUDA_MIN_DRIVER, {"13.3": 580, "13.4": 580})
        self.assertEqual(ds.VLLM_MIN_DRIVER, 575)
        self.assertEqual(ds.DRIVER_MIN_CUDA, {})


class TestDeploymentState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.tmp.name, "state.json")
        self._patch = patch.object(ds, "STATE_FILE", self.state_file)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def test_defaults_persist_and_reload(self):
        state = ds.DeploymentState()
        self.assertEqual(state.get()["mirrors"]["hf"], "https://hf-mirror.com")
        state.update_section("selected", driver="nvidia-driver-580")
        self.assertEqual(state.get()["selected"]["driver"], "nvidia-driver-580")
        reloaded = ds.DeploymentState()
        self.assertEqual(reloaded.get()["selected"]["driver"], "nvidia-driver-580")

    def test_update_section_ignores_unknown_keys(self):
        state = ds.DeploymentState()
        state.update_section("selected", bogus="x")
        self.assertNotIn("bogus", state.get()["selected"])

    def test_history_capped_at_100(self):
        state = ds.DeploymentState()
        for i in range(120):
            state.push_history({"ts": f"t{i}", "kind": "driver",
                                "title": f"op {i}", "status": "success"})
        history = state.get()["history"]
        self.assertEqual(len(history), 100)
        self.assertEqual(history[-1]["title"], "op 119")
        self.assertEqual(history[0]["title"], "op 20")

    def test_corrupt_file_falls_back_to_defaults(self):
        with open(self.state_file, "w", encoding="utf-8") as f:
            f.write("{not json")
        state = ds.DeploymentState()
        self.assertEqual(state.get()["selected"]["driver"], "")
        self.assertEqual(state.get()["history"], [])


def _task_entry(task_id: str, status: str) -> dict:
    return {
        "id": task_id,
        "kind": "driver",
        "title": f"task {task_id}",
        "status": status,
        "steps": [{"name": "s1", "status": "done"}],
        "current_step": 0,
        "error": "",
        "suggestion": "",
        "result": {},
        "created_at": "2026-01-01 00:00:00",
        "started_at": "2026-01-01 00:00:01",
        "finished_at": "2026-01-01 00:00:02" if status == "success" else "",
    }


class TestTaskManager(unittest.TestCase):
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
        self._executor = patch.object(
            ds, "get_remote_executor", return_value=MagicMock())
        self._executor.start()
        self.tasks_file = os.path.join(self.tmp.name, "tasks.json")

    def tearDown(self):
        self._executor.stop()
        self._patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def _wait_terminal(task: ds.Task, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if task.status in ds.TERMINAL_STATUSES:
                return
            time.sleep(0.01)
        raise AssertionError(f"task {task.id} did not finish (status={task.status})")

    def test_recover_marks_running_tasks_interrupted(self):
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            json.dump([_task_entry("dep_ok", "success"),
                       _task_entry("dep_run", "running")], f)
        mgr = ds.DeploymentTaskManager()
        recovered = mgr.get("dep_run")
        self.assertEqual(recovered.status, "interrupted")
        self.assertEqual(recovered.error, "Interrupted by dashboard restart")
        untouched = mgr.get("dep_ok")
        self.assertEqual(untouched.status, "success")

    def test_submit_success_completes_steps(self):
        mgr = ds.DeploymentTaskManager()
        task = mgr.create("driver", "unit-test", ["step-a", "step-b"])

        def runner(ctx):
            with ctx.step(0):
                ctx.log("hello from step 0")
            with ctx.step(1):
                ctx.log("step 1 done")

        mgr.submit(task, runner)
        self._wait_terminal(task)
        self.assertEqual(task.status, "success")
        self.assertEqual([s["status"] for s in task.steps], ["done", "done"])
        self.assertIn("hello from step 0", "\n".join(mgr.tail(task.id)))

    def test_submit_deployment_error_sets_failure_and_suggestion(self):
        mgr = ds.DeploymentTaskManager()
        task = mgr.create("vllm", "unit-test", ["step-a"])

        def runner(ctx):
            with ctx.step(0):
                raise ds.DeploymentError("No space left on device")

        mgr.submit(task, runner)
        self._wait_terminal(task)
        self.assertEqual(task.status, "failed")
        self.assertIn("No space left on device", task.error)
        self.assertIn("disk", task.suggestion.lower())

    def test_cancel_between_steps(self):
        mgr = ds.DeploymentTaskManager()
        task = mgr.create("driver", "unit-test", ["s1", "s2"])
        started = threading.Event()

        def runner(ctx):
            started.set()
            time.sleep(0.15)
            ctx.check_cancelled()

        mgr.submit(task, runner)
        started.wait(2)
        self.assertTrue(mgr.cancel(task.id))
        self._wait_terminal(task)
        self.assertEqual(task.status, "cancelled")
        # never-reached steps are skipped
        self.assertEqual([s["status"] for s in task.steps], ["skipped", "skipped"])

    def test_read_log_incremental_offsets(self):
        mgr = ds.DeploymentTaskManager()
        task = mgr.create("driver", "unit-test", ["s1"])
        mgr.log(task.id, "line1")
        first, offset1 = mgr.read_log(task.id, 0)
        self.assertIn("line1", first)
        self.assertGreater(offset1, 0)

        mgr.log(task.id, "line2")
        second, offset2 = mgr.read_log(task.id, offset1)
        self.assertIn("line2", second)
        self.assertNotIn("line1", second)
        self.assertGreater(offset2, offset1)

    def test_pending_driver_task(self):
        mgr = ds.DeploymentTaskManager()
        task = mgr.create("driver", "pending", ["s1"])
        task.status = "awaiting_reboot"
        task.result = {"package": "nvidia-driver-580"}
        self.assertEqual(mgr.pending_driver_task().id, task.id)

    def test_persisted_tasks_capped_at_max(self):
        mgr = ds.DeploymentTaskManager()
        for i in range(ds.DeploymentTaskManager.MAX_TASKS + 5):
            mgr.create("driver", f"task {i}", ["s1"])
        with open(self.tasks_file, encoding="utf-8") as f:
            entries = json.load(f)
        self.assertEqual(len(entries), ds.DeploymentTaskManager.MAX_TASKS)


class TestStateEndpoint(unittest.TestCase):
    def _get_state(self, current_driver: str, selected_cuda: str = "") -> dict:
        from fastapi.testclient import TestClient

        from backend.main import app
        executor = MagicMock()
        executor.get_driver_list.return_value = {
            "success": True, "current_driver": current_driver}
        state_data = json.loads(json.dumps(ds._default_state()))
        state_data["selected"]["cuda"] = selected_cuda
        with patch.object(api_dep, "get_remote_executor", return_value=executor), \
             patch.object(ds.deployment_state, "data", state_data):
            client = TestClient(app)
            resp = client.get("/api/v1/deployment/state")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_unlocked_when_driver_and_cuda_selected(self):
        body = self._get_state("580.65.06", selected_cuda="13.3")
        self.assertEqual(body["current_driver_major"], 580)
        self.assertFalse(body["locks"]["cuda"])
        self.assertFalse(body["locks"]["vllm"])

    def test_locked_when_no_driver(self):
        body = self._get_state("")
        self.assertIsNone(body["current_driver_major"])
        self.assertTrue(body["locks"]["cuda"])
        self.assertTrue(body["locks"]["vllm"])


class TestDriversEndpoint(unittest.TestCase):
    def _get_drivers(self, installed_packages, drivers=None):
        from fastapi.testclient import TestClient

        from backend.main import app
        if drivers is None:
            drivers = [
                {"package": "nvidia-driver-595", "version": 595, "tags": []},
                {"package": "nvidia-driver-595-open", "version": 595, "tags": []},
                {"package": "nvidia-driver-595-server", "version": 595, "tags": []},
                {"package": "nvidia-driver-595-server-open", "version": 595, "tags": []},
                {"package": "nvidia-driver-580-server", "version": 580, "tags": []},
            ]
        executor = MagicMock()
        executor.get_driver_list.return_value = {
            "success": True,
            "gpu_models": ["GB206 [GeForce RTX 5060 Ti]"],
            "drivers": drivers,
            "current_driver": "595.91.07",
            "installed_packages": installed_packages,
        }
        config = MagicMock()
        config.host = "10.131.1.7"
        with patch.object(api_dep, "get_remote_executor", return_value=executor), \
             patch.object(api_dep, "config_manager") as cm:
            cm.get_config.return_value = config
            client = TestClient(app)
            resp = client.get("/api/v1/deployment/drivers")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_marks_exact_installed_packages_only(self):
        # the probe returns only genuinely installed (ii) packages, so in the
        # real scenario just the concrete open variant is present
        body = self._get_drivers(["nvidia-driver-595-open"])
        flags = {r["package"]: r["installed"] for r in body["drivers"]}
        self.assertTrue(flags["nvidia-driver-595-open"])
        # same major version but not the installed variant -> no mark
        self.assertFalse(flags["nvidia-driver-595"])
        self.assertFalse(flags["nvidia-driver-595-server"])
        self.assertFalse(flags["nvidia-driver-595-server-open"])
        self.assertFalse(flags["nvidia-driver-580-server"])
        self.assertEqual(body["current_major"], 595)

    def test_no_version_fallback_when_dpkg_empty(self):
        # e.g. a .run-installed driver leaves no apt record: mark nothing
        # rather than flagging every variant sharing the major version
        body = self._get_drivers([])
        self.assertFalse(any(r["installed"] for r in body["drivers"]))
        self.assertEqual(body["current_major"], 595)

    def test_sorted_newest_version_first(self):
        # ubuntu-drivers devices output order is unstable run-to-run; the
        # endpoint must impose a deterministic order: newest major first,
        # versionless rows last, package name breaks ties
        rows = [
            {"package": "nvidia-driver-open", "version": None, "tags": []},
            {"package": "nvidia-driver-595-server", "version": 595, "tags": []},
            {"package": "xserver-xorg-video-nouveau", "version": None,
             "tags": [], "nouveau": True},
            {"package": "nvidia-driver-595", "version": 595, "tags": []},
            {"package": "nvidia-driver-580-server", "version": 580, "tags": []},
            {"package": "nvidia-driver-595-open", "version": 595, "tags": []},
        ]
        body = self._get_drivers([], drivers=rows)
        self.assertEqual(
            [r["package"] for r in body["drivers"]],
            [
                "nvidia-driver-595",
                "nvidia-driver-595-open",
                "nvidia-driver-595-server",
                "nvidia-driver-580-server",
                "nvidia-driver-open",
                "xserver-xorg-video-nouveau",
            ],
        )


class TestCudaEndpoint(unittest.TestCase):
    """current_toolkit: the probe now emits a bare nvcc version or the
    NO_NVCC sentinel — the sentinel must never reach the UI."""

    def _get_cuda(self, nvcc_stdout: str):
        from fastapi.testclient import TestClient

        from backend.main import app
        executor = MagicMock()
        executor.get_driver_list.return_value = {
            "success": True,
            "current_driver": "595.91.07",
        }
        executor.execute.return_value = {
            "success": True, "stdout": nvcc_stdout, "stderr": "", "returncode": 0}
        config = MagicMock()
        config.host = "10.131.1.7"
        state = MagicMock()
        state.get.return_value = ds._default_state()
        with patch.object(api_dep, "get_remote_executor", return_value=executor), \
             patch.object(api_dep, "config_manager") as cm, \
             patch.object(ds, "deployment_state", state):
            cm.get_config.return_value = config
            client = TestClient(app)
            resp = client.get("/api/v1/deployment/cuda")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_toolkit_version_parsed(self):
        body = self._get_cuda("13.0\n")
        self.assertEqual(body["current_toolkit"], "13.0")
        self.assertEqual(body["driver_major"], 595)
        # flipped robust default
        self.assertTrue(body["install_system"])

    def test_no_nvcc_sentinel_becomes_empty(self):
        body = self._get_cuda("NO_NVCC\n")
        self.assertEqual(body["current_toolkit"], "")


class TestPreflightPythonBounds(unittest.TestCase):
    """vLLM 0.29.0 requires_python is >=3.10,<3.15 (PyPI metadata) — the
    preflight check must enforce that range, not the old 3.9+ floor."""

    def _preflight(self, python: str) -> dict[str, dict]:
        from fastapi.testclient import TestClient

        from backend.main import app
        executor = MagicMock()
        executor.get_preflight.return_value = {
            "success": True,
            "os": "Ubuntu 26.04.1 LTS",
            "kernel": "7.0.0-31-generic",
            "gpus": [{"index": "0", "name": "GB206 [GeForce RTX 5060 Ti]",
                      "driver": "595.91.07", "memory": "16310 MiB"}],
            "gpu_missing": False,
            "cuda": "13.0",
            "python": python,
            "uv": "0.12.15",
            "uv_missing": False,
            "disk": ["796981740", "/"],
            "driver_tool": True,
            "net": {"https://pypi.org/simple/": "200"},
        }
        config = MagicMock()
        config.host = "10.131.1.7"
        with patch.object(api_dep, "get_remote_executor", return_value=executor), \
             patch.object(api_dep, "config_manager") as cm:
            cm.get_config.return_value = config
            client = TestClient(app)
            resp = client.get("/api/v1/deployment/preflight")
        self.assertEqual(resp.status_code, 200)
        return {i["key"]: i for i in resp.json()["items"]}

    def test_below_310_fails(self):
        self.assertEqual(self._preflight("3.9.19")["python"]["status"], "fail")

    def test_310_to_314_ok(self):
        for v in ("3.10.12", "3.12.7", "3.14.4"):
            self.assertEqual(self._preflight(v)["python"]["status"], "ok", v)

    def test_315_and_above_fails(self):
        self.assertEqual(self._preflight("3.15.0")["python"]["status"], "fail")


class TestGetPreflight(unittest.TestCase):
    """Sentinel discipline: NO_NVCC / NO_UV / "command not found" must never
    reach the UI — the backend emits stable, translatable English strings."""

    def _probe(self, stdout: str):
        import types

        return types.MethodType(
            ops.DeploymentOps.get_preflight, _FakeExecutor(stdout))()

    def _stdout(self, cuda: str = "NO_NVCC", uv: str = "NO_UV") -> str:
        return (
            "== OS ==\nUbuntu 26.04.1 LTS\n"
            "== KERNEL ==\n7.0.0-31-generic\n"
            "== GPU ==\n0, GB206 [GeForce RTX 5060 Ti], 595.91.07, 16310 MiB\n"
            f"== CUDA ==\n{cuda}\n"
            "== PYTHON ==\nPython 3.14.4\n"
            f"== UV ==\n{uv}\n"
            "== DISK ==\n796981740\t/\n"
            "== DRIVER_TOOL ==\n/usr/bin/ubuntu-drivers\n"
            "== NET ==\nNET https://pypi.org/simple/ 200\n"
            "== END ==\nDONE"
        )

    def test_uv_found_reports_version_only(self):
        res = self._probe(self._stdout(uv="uv 0.12.15 (x86_64-unknown-linux-gnu)"))
        self.assertEqual(res["uv"], "0.12.15")
        self.assertFalse(res["uv_missing"])

    def test_uv_command_not_found_is_flagged_missing(self):
        res = self._probe(self._stdout(uv="bash: line 1: uv: command not found\nNO_UV"))
        self.assertEqual(res["uv"], "")
        self.assertTrue(res["uv_missing"])

    def test_cuda_no_nvcc_becomes_translatable_value(self):
        res = self._probe(self._stdout(cuda="NO_NVCC"))
        self.assertEqual(
            res["cuda"], "not installed (optional — vLLM ships its own runtime)")

    def test_cuda_version_parsed(self):
        # the probe emits a bare version (nvcc -V reports "release X.Y",
        # never the "CUDA Version" banner — that belongs to nvidia-smi)
        res = self._probe(self._stdout(cuda="12.9"))
        self.assertEqual(res["cuda"], "12.9")

    def test_preflight_command_extends_path_for_uv(self):
        self.assertIn(ops.UV_PATH_PREFIX, ops.build_preflight_command())


class _FakeVllmExecutor:
    """Fake executor that records commands (for the vllm env probe)."""

    def __init__(self, stdout: str):
        self._stdout = stdout
        self.commands: list[str] = []

    def execute(self, command, timeout=None, stdin_data=None):
        self.commands.append(command)
        return {"success": True, "stdout": self._stdout, "stderr": "", "returncode": 0}

    def _get_activate_cmd(self) -> str:
        return ""


class TestGetVllmEnv(unittest.TestCase):
    def test_uv_probe_extends_path_and_parses_version(self):
        import types

        fake = _FakeVllmExecutor(
            "== UV ==\nuv 0.12.15 (x86_64-unknown-linux-gnu)\n== END ==\nDONE")
        res = types.MethodType(ops.DeploymentOps.get_vllm_env, fake)()
        self.assertEqual(res["uv"], "0.12.15")
        self.assertFalse(res["uv_missing"])
        # every uv invocation must carry the PATH extension
        self.assertTrue(
            all(ops.UV_PATH_PREFIX in c for c in fake.commands),
            f"commands missing {ops.UV_PATH_PREFIX!r}: {fake.commands}")

    def test_uv_missing_flag_when_command_not_found(self):
        import types

        fake = _FakeVllmExecutor(
            "== UV ==\nNO_UV\n== END ==\nDONE")
        res = types.MethodType(ops.DeploymentOps.get_vllm_env, fake)()
        self.assertEqual(res["uv"], "")
        self.assertTrue(res["uv_missing"])


class TestBuildVllmInstallCommands(unittest.TestCase):
    def _deps(self, **kw) -> list[str]:
        payload = {"env_mode": "existing", "venv_name": ".vllm",
                   "version": "latest", "runtime_mode": "builtin"}
        payload.update(kw)
        _, _, deps = ds._build_vllm_install_commands(
            payload, "$HOME/.vllm", {"pypi": ""})
        return deps

    def test_nccl_follows_effective_torch_family(self):
        # built-in runtime keeps the default cu12 family even when the CUDA
        # tab selected a 13.x toolkit
        deps = self._deps(nccl=True, cuda_version="13.3")
        self.assertEqual(len(deps), 1)
        self.assertIn("nvidia-nccl-cu12", deps[0])
        # system runtime with CUDA 13.x picks the cu13 family (exists on PyPI)
        deps = self._deps(nccl=True, runtime_mode="system", cuda_version="13.3")
        self.assertIn("nvidia-nccl-cu13", deps[0])
        # system runtime with CUDA 12.x (custom field) stays on cu12
        deps = self._deps(nccl=True, runtime_mode="system", cuda_version="12.9")
        self.assertIn("nvidia-nccl-cu12", deps[0])

    def test_system_torch_index_maps_to_cuda_family(self):
        # torch wheels are published per CUDA family, not per toolkit minor
        # version: /whl/cu133 is an S3 AccessDenied page and /whl/cu134 is a
        # generic fallback listing, so every 13.x selection must use cu130.
        base = {"env_mode": "existing", "venv_name": ".vllm",
                "version": "latest", "runtime_mode": "system"}
        for ver, want in (("13.3", "whl/cu130"), ("13.4", "whl/cu130"),
                          ("12.9", "whl/cu129")):
            _, install, _ = ds._build_vllm_install_commands(
                {**base, "cuda_version": ver}, "$HOME/.vllm", {"pypi": ""})
            self.assertIn(want, install)
        # built-in runtime carries no torch index at all
        _, install, _ = ds._build_vllm_install_commands(
            {**base, "runtime_mode": "builtin", "cuda_version": "13.3"},
            "$HOME/.vllm", {"pypi": ""})
        self.assertNotIn("download.pytorch.org", install)

    def test_no_nccl_no_deps(self):
        self.assertEqual(self._deps(nccl=False, flashinfer=False), [])


class TestProbeInThread(unittest.TestCase):
    """Wall-clock guard: a blocked SSH probe must surface as HTTP 504.

    The executor's per-command timeout only bounds channel open; a stalled
    remote (sshd not sending its protocol banner) can hold stdout.read()
    indefinitely. Read endpoints wrap probes in _probe_in_thread so they
    fail fast instead of hanging the whole modal.
    """

    def test_returns_result(self):
        self.assertEqual(asyncio.run(api_dep._probe_in_thread(lambda: 42, 5)), 42)

    def test_wall_clock_timeout_raises_504(self):
        from fastapi import HTTPException

        def _block():
            time.sleep(1)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api_dep._probe_in_thread(_block, 0.1))
        self.assertEqual(ctx.exception.status_code, 504)


if __name__ == "__main__":
    unittest.main()
