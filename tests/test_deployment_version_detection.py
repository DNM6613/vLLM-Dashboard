"""Version discovery for the deployment tabs (2026-10-24).

CUDA: live NVIDIA apt repo probe (which cuda-toolkit-X.Y the repo offers)
replaces the hard-coded 3-item list; vLLM: the PyPI stable release list
replaces "latest or hand-typed only". Both are TTL-cached (the endpoints
are polled every 5 s) and degrade gracefully — static list (CUDA) / empty
list (vLLM) on probe failure.
"""

import unittest
from unittest.mock import MagicMock, patch

from backend.api import deployment as api_dep
from backend.executor.remote.deployment_commands import (
    CUDA_REPO_VERSIONS_CMD,
    parse_cuda_repo_versions,
)
from backend.service import deployment_service as ds
from backend.service import deployment_version_probe as vp
from backend.service.deployment_common import _CUDA_VERSIONS


class TestParseCudaRepoVersions(unittest.TestCase):
    def test_dedupes_and_sorts_ascending(self):
        raw = "\n".join([
            "Package: cuda-toolkit-13-0",
            "Package: cuda-toolkit-12-9",
            "Package: cuda-toolkit-13-0",  # dup across distro indices
            "Package: cuda-toolkit-13-2",
            "Package: cuda-toolkit-13-4",
            "VDB_REPO_DONE",
        ])
        self.assertEqual(
            parse_cuda_repo_versions(raw), ["12.9", "13.0", "13.2", "13.4"])

    def test_excludes_non_toolkit_and_odd_shaped_names(self):
        raw = "\n".join([
            "Package: cuda-toolkit-13",       # single component
            "Package: cuda-toolkit-13-0-13-0",  # extra component
            "Package: cuda-nvcc-12-9",
            "Package: libcudnn9-cuda-12",
            "Version: 12.9.1-1",
            "",
        ])
        self.assertEqual(parse_cuda_repo_versions(raw), [])

    def test_version_sort_not_lexicographic(self):
        raw = "\n".join([
            "Package: cuda-toolkit-13-9",
            "Package: cuda-toolkit-13-10",
            "Package: cuda-toolkit-12-9",
        ])
        self.assertEqual(
            parse_cuda_repo_versions(raw), ["12.9", "13.9", "13.10"])

    def test_command_shape(self):
        self.assertIn("developer.download.nvidia.com/compute/cuda/repos",
                      CUDA_REPO_VERSIONS_CMD)
        # the 26.04 repo only carries 13.3/13.4 — ubuntu2404 must always
        # be probed for the 12.x/13.0-13.2 toolkits
        self.assertIn("ubuntu2404", CUDA_REPO_VERSIONS_CMD)
        # geo-redirect to a CDN — curl must follow redirects
        self.assertIn("curl -fsSL", CUDA_REPO_VERSIONS_CMD)


class _FakeExecutor:
    def __init__(self, stdout="", success=True):
        self.stdout = stdout
        self.success = success
        self.calls: list[str] = []

    def execute(self, command, timeout=None, stdin_data=None):
        self.calls.append(command)
        return {"success": self.success, "stdout": self.stdout,
                "stderr": "", "returncode": 0}


class TestProbeCudaRepoVersions(unittest.TestCase):
    def setUp(self):
        vp.reset_caches()

    def tearDown(self):
        vp.reset_caches()

    def test_success_caches_and_flags_source(self):
        ex = _FakeExecutor(stdout="Package: cuda-toolkit-13-2\n"
                                   "Package: cuda-toolkit-13-4\n")
        versions, source = vp.probe_cuda_repo_versions(ex)
        self.assertEqual((versions, source), (["13.4", "13.2"], "repo"))
        self.assertEqual(len(ex.calls), 1)
        # cache hit: no second SSH round-trip (the 5 s poll must not ride it)
        versions, source = vp.probe_cuda_repo_versions(ex)
        self.assertEqual(source, "repo")
        self.assertEqual(len(ex.calls), 1)

    def test_unlisted_versions_hidden_and_newest_first(self):
        # 13.9 is not indexed in CUDA_MIN_DRIVER — excluded (user ruling
        # 2026-10-24); the indexed versions are ordered newest first
        ex = _FakeExecutor(stdout="\n".join([
            "Package: cuda-toolkit-12-8",
            "Package: cuda-toolkit-12-5",
            "Package: cuda-toolkit-13-9",
            "Package: cuda-toolkit-13-0",
            "Package: cuda-toolkit-13-4",
        ]))
        versions, source = vp.probe_cuda_repo_versions(ex)
        self.assertEqual(
            (versions, source), (["13.4", "13.0", "12.8", "12.5"], "repo"))

    def test_probe_failure_falls_back_with_negative_window(self):
        ex = _FakeExecutor(success=False)
        versions, source = vp.probe_cuda_repo_versions(ex)
        self.assertEqual(versions, list(_CUDA_VERSIONS))
        self.assertEqual(source, "fallback")
        # negative window: the poll must not hammer the dead upstream
        vp.probe_cuda_repo_versions(ex)
        self.assertEqual(len(ex.calls), 1)

    def test_empty_output_falls_back(self):
        ex = _FakeExecutor(stdout="VDB_REPO_DONE\n")
        _, source = vp.probe_cuda_repo_versions(ex)
        self.assertEqual(source, "fallback")

    def test_cached_cuda_versions_or_fallback(self):
        ex = _FakeExecutor(stdout="Package: cuda-toolkit-13-4\n")
        vp.probe_cuda_repo_versions(ex)
        self.assertEqual(vp.cached_cuda_versions_or_fallback(), ["13.4"])
        vp.reset_caches()
        self.assertEqual(vp.cached_cuda_versions_or_fallback(),
                         list(_CUDA_VERSIONS))


class TestParseVllmReleases(unittest.TestCase):
    def test_stable_only_newest_first_capped(self):
        releases = {f"0.{i:02d}.0": ["x.whl"] for i in range(1, 30)}
        releases["0.29.1rc1"] = ["x.whl"]
        releases["0.30.0.dev1"] = []
        out = vp.parse_vllm_releases(releases)
        self.assertEqual(out[0], "0.29.0")
        self.assertEqual(len(out), 20)
        self.assertNotIn("0.29.1rc1", out)
        self.assertNotIn("0.30.0.dev1", out)

    def test_empty_and_none(self):
        self.assertEqual(vp.parse_vllm_releases(None), [])
        self.assertEqual(vp.parse_vllm_releases({}), [])


class TestGetVllmAvailableVersions(unittest.TestCase):
    def setUp(self):
        vp.reset_caches()

    def tearDown(self):
        vp.reset_caches()

    def _resp(self, json_data):
        r = MagicMock()
        r.raise_for_status.return_value = None
        r.json.return_value = json_data
        return r

    def test_success_then_cache(self):
        releases = {"0.33.0": [], "0.32.0": [], "0.31.0": []}
        with patch.object(vp.httpx, "get",
                          return_value=self._resp({"releases": releases})) as m:
            first = vp.get_vllm_available_versions("")
            second = vp.get_vllm_available_versions("")
        self.assertEqual(first, ["0.33.0", "0.32.0", "0.31.0"])
        self.assertEqual(second, first)
        # TTL cache: no second network fetch
        self.assertEqual(m.call_count, 1)
        self.assertEqual(m.call_args_list[0].args[0], vp.PYPI_JSON_URL)

    def test_mirror_fallback_when_pypi_down(self):
        releases = {"0.33.0": []}

        def _get(url, **kw):
            if url == vp.PYPI_JSON_URL:
                raise vp.httpx.ConnectError("blocked", request=MagicMock())
            return self._resp({"releases": releases})

        with patch.object(vp.httpx, "get", side_effect=_get) as m:
            out = vp.get_vllm_available_versions(
                "https://pypi.tuna.tsinghua.edu.cn/simple/")
        self.assertEqual(out, ["0.33.0"])
        self.assertEqual(
            m.call_args_list[1].args[0],
            "https://pypi.tuna.tsinghua.edu.cn/pypi/vllm/json")

    def test_all_sources_down_yields_empty(self):
        with patch.object(vp.httpx, "get",
                          side_effect=vp.httpx.ConnectError(
                              "down", request=MagicMock())):
            self.assertEqual(vp.get_vllm_available_versions(""), [])


class TestCudaEndpointVersions(unittest.TestCase):
    """/cuda serves the live repo list; probe failure degrades to the
    static list — never a broken endpoint."""

    def setUp(self):
        vp.reset_caches()

    def tearDown(self):
        vp.reset_caches()

    def _get_cuda(self, nvcc_stdout: str, repo_stdout: str) -> dict:
        from fastapi.testclient import TestClient

        from backend.main import app
        executor = MagicMock()
        executor.get_driver_list.return_value = {
            "success": True, "current_driver": "595.91.07"}

        def _exec(cmd, timeout=None, stdin_data=None):
            if "cuda/repos" in cmd:
                return {"success": True, "stdout": repo_stdout,
                        "stderr": "", "returncode": 0}
            return {"success": True, "stdout": nvcc_stdout,
                    "stderr": "", "returncode": 0}
        executor.execute.side_effect = _exec
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

    def test_detected_versions_reach_the_ui(self):
        body = self._get_cuda(
            "13.0\n",
            "Package: cuda-toolkit-13-2\nPackage: cuda-toolkit-13-4\n")
        self.assertEqual(body["versions"], ["13.4", "13.2"])
        self.assertEqual(body["versions_source"], "repo")

    def test_repo_probe_failure_degrades_to_static_list(self):
        body = self._get_cuda("13.0\n", "")
        self.assertEqual(body["versions"], list(_CUDA_VERSIONS))
        self.assertEqual(body["versions_source"], "fallback")

    def test_existing_toolkit_fields_unchanged(self):
        body = self._get_cuda("13.0\n", "Package: cuda-toolkit-13-2\n")
        self.assertEqual(body["current_toolkit"], "13.0")
        self.assertEqual(body["driver_major"], 595)


class TestVllmEndpointVersions(unittest.TestCase):
    def setUp(self):
        vp.reset_caches()

    def tearDown(self):
        vp.reset_caches()

    def test_available_versions_and_latest(self):
        from fastapi.testclient import TestClient

        from backend.main import app
        executor = MagicMock()
        executor.get_vllm_env.return_value = {
            "success": True, "uv": "0.12.15", "uv_missing": False,
            "pythons": ["3.12"], "venvs": ["/home/uforce/.vllm"],
            "vllm_version": "0.32.0"}
        executor.execute.return_value = {
            "success": True, "stdout": "NO_NVCC\n", "stderr": "",
            "returncode": 0}
        config = MagicMock()
        config.host = "10.131.1.7"
        state = MagicMock()
        state.get.return_value = ds._default_state()
        with patch.object(api_dep, "get_remote_executor", return_value=executor), \
             patch.object(api_dep, "config_manager") as cm, \
             patch.object(ds, "deployment_state", state), \
             patch.object(vp, "get_vllm_available_versions",
                          return_value=["0.33.0", "0.32.0"]):
            cm.get_config.return_value = config
            client = TestClient(app)
            resp = client.get("/api/v1/deployment/vllm")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["available_versions"], ["0.33.0", "0.32.0"])
        self.assertEqual(body["latest_version"], "0.33.0")
        # cuda_versions rides the (empty) repo-probe cache -> static list
        self.assertEqual(body["cuda_versions"], list(_CUDA_VERSIONS))
