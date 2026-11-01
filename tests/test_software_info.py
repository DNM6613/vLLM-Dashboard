"""get_software_info: the CUDA badge shows the vLLM-side CUDA *runtime*
(venv torch.version.cuda), not the system nvcc toolkit.

Regression coverage for R1: the probe must not reintroduce the old
nvcc / readlink toolkit detection and must report
``torch.version.cuda`` as the ``cuda_runtime`` field.
"""
import unittest

from backend.executor.remote.monitor_ops import MonitorOps


class _FakeOps(MonitorOps):
    host = "h"
    port = 22
    username = "u"

    def __init__(self, stdout: str, success: bool = True):
        self._stdout = stdout
        self._success = success
        self.last_command: str | None = None
        self.last_timeout: float | None = None

    def _get_activate_cmd(self):
        return "source /venv/bin/activate && "

    def execute(self, command, timeout=None, stdin_data=None):
        self.last_command = command
        self.last_timeout = timeout
        return {"success": self._success, "stdout": self._stdout,
                "stderr": "boom"}


class TestSoftwareInfo(unittest.TestCase):
    def test_full_line_parses_four_fields(self):
        ops = _FakeOps("Ubuntu 26.04|615.78.08|13.0|0.33.0")
        info = ops.get_software_info()
        self.assertEqual(info, {
            "os": "Ubuntu 26.04",
            "gpu_driver": "615.78.08",
            "cuda_runtime": "13.0",
            "vllm": "0.33.0",
        })

    def test_empty_segments_fall_back_to_unknown(self):
        ops = _FakeOps("Ubuntu|615.78||")
        info = ops.get_software_info()
        self.assertEqual(info["os"], "Ubuntu")
        self.assertEqual(info["gpu_driver"], "615.78")
        self.assertEqual(info["cuda_runtime"], "Unknown")
        self.assertEqual(info["vllm"], "Unknown")

    def test_failed_execute_returns_all_unknown(self):
        ops = _FakeOps("", success=False)
        info = ops.get_software_info()
        self.assertEqual(info, {
            "os": "Unknown", "gpu_driver": "Unknown",
            "cuda_runtime": "Unknown", "vllm": "Unknown",
        })

    def test_command_probes_torch_runtime_not_system_toolkit(self):
        ops = _FakeOps("Ubuntu 26.04|615.78.08|13.0|0.33.0")
        ops.get_software_info()
        cmd = ops.last_command or ""
        self.assertIn("torch.version.cuda", cmd)
        self.assertNotIn("nvcc", cmd)
        self.assertNotIn("/usr/local/cuda", cmd)
        self.assertNotIn("readlink", cmd)
        self.assertEqual(ops.last_timeout, 60)


if __name__ == "__main__":
    unittest.main()
