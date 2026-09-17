import time
import unittest
from unittest.mock import MagicMock, patch

from backend.service import model_service

LOG_FILE = "/tmp/model_download_test_repo.log"


def _status_stdout(pid: str, mtime: int, tail: str, log_size: int = 12345) -> str:
    head = f"EXISTS\nSIZE={log_size}\nMTIME={mtime}\nPID={pid}\n"
    return head + "---TAIL---\n" + tail


class TestGetDownloadStatus(unittest.TestCase):
    def setUp(self):
        model_service._dl_growth.clear()
        model_service._dl_total_cache.clear()

    def _run(self, stdout: str, size_bytes: int = 0, total_bytes: int = 0) -> dict:
        executor = MagicMock()
        executor.execute.return_value = {"success": True, "stdout": stdout, "stderr": ""}
        executor.get_download_size.return_value = {"success": True, "size_bytes": size_bytes}
        executor.get_download_total_size.return_value = {
            "success": total_bytes > 0, "total_bytes": total_bytes}
        with patch("backend.service.model_service.get_remote_executor", return_value=executor):
            return model_service.get_download_status(
                LOG_FILE, repo="org/name", save_path="~/models")

    def test_running_uses_byte_progress_not_file_count(self):
        # The CLI's textual % is a file-count ratio ("12%"), but the on-disk
        # bytes say 1GiB of 2GiB — progress must follow the bytes.
        stdout = _status_stdout(pid="4321", mtime=int(time.time()), tail="Fetching 3 files: 12%")
        result = self._run(stdout, size_bytes=1024 ** 3, total_bytes=2 * 1024 ** 3)
        self.assertEqual(result["status"], "downloading")
        self.assertEqual(result["size_bytes"], 1024 ** 3)
        self.assertEqual(result["total_size"], 2 * 1024 ** 3)
        self.assertAlmostEqual(result["progress"], 50.0, places=1)

    def test_running_is_stalled_on_byte_stagnation(self):
        # Same size as 5 minutes ago: no byte growth while the process is
        # alive — a hung download.
        stdout = _status_stdout(pid="4321", mtime=int(time.time()), tail="Fetching 3 files: 40%")
        model_service._dl_growth[LOG_FILE] = ("4321", 2 * 1024 ** 3, time.time() - 300)
        result = self._run(stdout, size_bytes=2 * 1024 ** 3, total_bytes=22 * 1024 ** 3)
        self.assertEqual(result["status"], "stalled")
        self.assertGreater(result["stalled_secs"], 180)
        self.assertEqual(result["size_bytes"], 2 * 1024 ** 3)

    def test_byte_growth_resets_stall_even_with_old_log_mtime(self):
        # Regression: the `hf` CLI writes one log line per *file*, so the log
        # mtime is silent for minutes while a large file transfers. Bytes
        # grew (1GiB -> 2GiB) since the last poll, so this is NOT a stall
        # even though the log mtime is 5 minutes old.
        stdout = _status_stdout(pid="4321", mtime=int(time.time()) - 300, tail="Fetching 3 files: 40%")
        model_service._dl_growth[LOG_FILE] = ("4321", 1024 ** 3, time.time() - 300)
        result = self._run(stdout, size_bytes=2 * 1024 ** 3, total_bytes=22 * 1024 ** 3)
        self.assertEqual(result["status"], "downloading")
        self.assertAlmostEqual(result["progress"], 2 / 22 * 100, places=1)

    def test_new_pid_resets_growth_baseline(self):
        # Re-download of the same repo: new process, smaller size than the
        # previous run — must not be reported as stalled immediately.
        stdout = _status_stdout(pid="9999", mtime=int(time.time()), tail="Fetching 19 files: 5%")
        model_service._dl_growth[LOG_FILE] = ("4321", 20 * 1024 ** 3, time.time() - 400)
        result = self._run(stdout, size_bytes=1024 ** 2, total_bytes=22 * 1024 ** 3)
        self.assertEqual(result["status"], "downloading")

    def test_exited_with_completion_marker_is_complete(self):
        tail = "Done: /home/u/.cache/huggingface/hub/models--org--name/snapshots/abc123"
        stdout = _status_stdout(pid="", mtime=int(time.time()), tail=tail)
        result = self._run(stdout, size_bytes=22 * 1024 ** 3, total_bytes=22 * 1024 ** 3)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["total_size"], 22 * 1024 ** 3)

    def test_exited_with_fatal_error_is_failed(self):
        tail = "Traceback (most recent call last):\n  ConnectionError: reset"
        stdout = _status_stdout(pid="", mtime=int(time.time()), tail=tail)
        result = self._run(stdout)
        self.assertEqual(result["status"], "failed")
        self.assertIn("ConnectionError", result["reason"])

    def test_exited_without_marker_or_error_is_stopped(self):
        # Process died mid-download: no completion marker, no detected error.
        # Must NOT be reported as "complete" (the model may be partial).
        stdout = _status_stdout(pid="", mtime=int(time.time()), tail="Downloading 35%")
        result = self._run(stdout, size_bytes=500 * 1024 ** 2)
        self.assertEqual(result["status"], "stopped")

    def test_missing_log_is_not_found(self):
        executor = MagicMock()
        executor.execute.return_value = {"success": True, "stdout": "NOT_FOUND", "stderr": ""}
        with patch("backend.service.model_service.get_remote_executor", return_value=executor):
            result = model_service.get_download_status("/tmp/model_download_x.log", repo="a/b")
        self.assertEqual(result["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
