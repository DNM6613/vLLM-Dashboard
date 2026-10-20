"""Per-GPU PCIe link fields + dmon -s t throughput parsing (Q1).

nvidia-smi is the only source (no DCGM/pynvml on the AI server); the
approach mirrors the Strata project: query-gpu link gen/width plus a
single-cycle `nvidia-smi dmon -s t -c 1` transport sensor for per-GPU
PCIe Rx/Tx MB/s.
"""
import unittest

from backend.executor.remote.monitor_ops import (
    _PCIE_MARKER,
    MonitorOps,
    _parse_dmon_pcie,
    _parse_nvidia_smi_lines,
)

_LEGACY_LINE = "0, GPU X, 16371, 15000, 1371, 45, 50.5, 150.0, 10, 5"
_PCIE_LINE = _LEGACY_LINE + ", 4, 8"


class _FakeOps(MonitorOps):
    host = "h"
    port = 22
    username = "u"

    def __init__(self, stdout: str, success: bool = True):
        self._stdout = stdout
        self._success = success
        self.last_command: str | None = None

    def execute(self, command, timeout=None, stdin_data=None):
        self.last_command = command
        return {"success": self._success, "stdout": self._stdout, "stderr": "boom"}


class TestNvidiaSmiPcieFields(unittest.TestCase):
    def test_line_with_pcie_fields(self):
        gpus = _parse_nvidia_smi_lines(_PCIE_LINE)
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0]["pcie_gen"], 4)
        self.assertEqual(gpus[0]["pcie_width"], 8)
        # existing fields unchanged
        self.assertEqual(gpus[0]["index"], 0)
        self.assertEqual(gpus[0]["temperature"], 45)

    def test_pcie_na_yields_none(self):
        line = "0, GPU X, 16371, 15000, 1371, 45, 50.5, 150.0, 10, 5, N/A, N/A"
        gpus = _parse_nvidia_smi_lines(line)
        self.assertIsNone(gpus[0]["pcie_gen"])
        self.assertIsNone(gpus[0]["pcie_width"])

    def test_legacy_line_without_pcie_fields(self):
        gpus = _parse_nvidia_smi_lines(_LEGACY_LINE)
        self.assertEqual(len(gpus), 1)
        self.assertIsNone(gpus[0]["pcie_gen"])
        self.assertIsNone(gpus[0]["pcie_width"])


class TestDmonPcieParse(unittest.TestCase):
    def test_parses_rows_and_skips_headers(self):
        lines = [
            "# gpu  rxpci  txpci",
            "# Idx   MB/s   MB/s",
            "    0      0      0",
            "    1      1      0",
            "    2     11      3",
            "    3      0      0",
        ]
        self.assertEqual(
            _parse_dmon_pcie(lines),
            {0: (0, 0), 1: (1, 0), 2: (11, 3), 3: (0, 0)},
        )

    def test_ignores_blank_and_garbage_lines(self):
        lines = ["   ", "# header", "x y z", "1 notanumber 2", " 2 "]
        self.assertEqual(_parse_dmon_pcie(lines), {})


class TestGetNvidiaSmiMergesDmon(unittest.TestCase):
    def test_merge_by_index(self):
        stdout = (
            "0, GPU A, 16371, 15000, 1371, 45, 50.5, 150.0, 10, 5, 4, 8\n"
            "1, GPU B, 16371, 15000, 1371, 45, 50.5, 150.0, 10, 5, 4, 8\n"
            f"{_PCIE_MARKER}\n"
            "# gpu  rxpci  txpci\n"
            "# Idx   MB/s   MB/s\n"
            "    0      5      2\n"
            "    1      9      7\n"
        )
        ops = _FakeOps(stdout)
        result = ops.get_nvidia_smi()
        self.assertIsNone(result["error"])
        gpus = result["gpus"]
        self.assertEqual(gpus[0]["pcie_rx_mbs"], 5)
        self.assertEqual(gpus[0]["pcie_tx_mbs"], 2)
        self.assertEqual(gpus[1]["pcie_rx_mbs"], 9)
        self.assertEqual(gpus[1]["pcie_tx_mbs"], 7)
        self.assertIn("nvidia-smi dmon -s t -c 1", ops.last_command)
        self.assertIn("pcie.link.gen.current", ops.last_command)

    def test_missing_marker_section_yields_none(self):
        stdout = "0, GPU A, 16371, 15000, 1371, 45, 50.5, 150.0, 10, 5, 4, 8\n"
        gpus = _FakeOps(stdout).get_nvidia_smi()["gpus"]
        self.assertIsNone(gpus[0]["pcie_rx_mbs"])
        self.assertIsNone(gpus[0]["pcie_tx_mbs"])

    def test_empty_dmon_section_yields_none(self):
        stdout = (
            "0, GPU A, 16371, 15000, 1371, 45, 50.5, 150.0, 10, 5, 4, 8\n"
            f"{_PCIE_MARKER}\n"
        )
        gpus = _FakeOps(stdout).get_nvidia_smi()["gpus"]
        self.assertIsNone(gpus[0]["pcie_rx_mbs"])

    def test_command_failure_returns_error(self):
        ops = _FakeOps("", success=False)
        result = ops.get_nvidia_smi()
        self.assertEqual(result["gpus"], [])
        self.assertEqual(result["error"], "boom")


class TestGetHardwareSnapshotSections(unittest.TestCase):
    SYSINFO_LINE = "CPU Model|4|12.34|4096,1024,3072|100,50,50|cpu stat line"

    def test_three_section_split(self):
        stdout = (
            "0, GPU A, 16371, 15000, 1371, 45, 50.5, 150.0, 10, 5, 4, 8\n"
            f"{_PCIE_MARKER}\n"
            "    0      5      2\n"
            "===VDB_SYSINFO===\n"
            + self.SYSINFO_LINE + "\n"
        )
        ops = _FakeOps(stdout)
        result = ops.get_hardware_snapshot()
        gpus = result["gpus"]
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0]["pcie_gen"], 4)
        self.assertEqual(gpus[0]["pcie_rx_mbs"], 5)
        self.assertEqual(result["cpu"]["count"], 4)

    def test_sysinfo_missing_still_parses_gpus_and_pcie(self):
        stdout = (
            "0, GPU A, 16371, 15000, 1371, 45, 50.5, 150.0, 10, 5, 4, 8\n"
            f"{_PCIE_MARKER}\n"
            "    0      5      2\n"
        )
        result = _FakeOps(stdout).get_hardware_snapshot()
        self.assertEqual(result["gpus"][0]["pcie_rx_mbs"], 5)
        self.assertIsNone(result["cpu"])

    def test_command_failure(self):
        result = _FakeOps("", success=False).get_hardware_snapshot()
        self.assertEqual(result["gpus"], [])
        self.assertEqual(result["error"], "boom")
