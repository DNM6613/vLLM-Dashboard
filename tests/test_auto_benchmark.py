"""Auto-benchmark: pending flags, result persistence, RUNNING-triggered runs."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.config.remote_client import config_manager
from backend.config.server_config import ServerConfig
from backend.controller.state_machine import state_machine
from backend.service import model_service as ms


class _AutoBenchBase(unittest.TestCase):
    def setUp(self):
        self._results_backup = (
            ms._BENCHMARK_RESULTS_FILE.read_text(encoding="utf-8")
            if ms._BENCHMARK_RESULTS_FILE.exists() else None
        )
        self._pending_backup = (
            ms._AUTO_BENCH_PENDING_FILE.read_text(encoding="utf-8")
            if ms._AUTO_BENCH_PENDING_FILE.exists() else None
        )

    def tearDown(self):
        for mid in list(state_machine.models):
            if mid.startswith("ab-test"):
                state_machine.remove_model(mid)
        with state_machine._lock:
            state_machine._dirty = False
            if state_machine._debounce_timer is not None:
                state_machine._debounce_timer.cancel()
                state_machine._debounce_timer = None
        for key in list(ms._benchmark_results):
            if key.startswith("ab-test"):
                del ms._benchmark_results[key]
        for key in list(ms._auto_bench_pending):
            if key.startswith("ab-test"):
                ms._auto_bench_pending.discard(key)
        for path, backup in ((ms._BENCHMARK_RESULTS_FILE, self._results_backup),
                             (ms._AUTO_BENCH_PENDING_FILE, self._pending_backup)):
            if backup is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_text(backup, encoding="utf-8")


class TestAutoBenchPending(_AutoBenchBase):
    def test_mark_and_take_once(self):
        ms._mark_auto_bench_pending("ab-test-p1")
        self.assertTrue(ms._take_auto_bench_pending("ab-test-p1"))
        self.assertFalse(ms._take_auto_bench_pending("ab-test-p1"))
        self.assertFalse(ms._take_auto_bench_pending("ab-test-p2"))

    def test_mark_idempotent(self):
        ms._mark_auto_bench_pending("ab-test-p1")
        ms._mark_auto_bench_pending("ab-test-p1")
        self.assertTrue(ms._take_auto_bench_pending("ab-test-p1"))
        self.assertFalse(ms._take_auto_bench_pending("ab-test-p1"))

    def test_pending_persisted_to_file(self):
        ms._mark_auto_bench_pending("ab-test-p1")
        data = json.loads(ms._AUTO_BENCH_PENDING_FILE.read_text(encoding="utf-8"))
        self.assertIn("ab-test-p1", data)
        ms._take_auto_bench_pending("ab-test-p1")
        data = json.loads(ms._AUTO_BENCH_PENDING_FILE.read_text(encoding="utf-8"))
        self.assertNotIn("ab-test-p1", data)


class TestBenchmarkResultStore(_AutoBenchBase):
    def test_record_get_remove(self):
        self.assertIsNone(ms._get_benchmark_result("ab-test-m"))
        ms._record_benchmark_result("ab-test-m", {
            "tokens_per_second": 69.5, "prompt_tokens": 10,
            "completion_tokens": 128, "elapsed_seconds": 1.85,
        })
        rec = ms._get_benchmark_result("ab-test-m")
        self.assertEqual(rec["tokens_per_second"], 69.5)
        self.assertIn("+00:00", rec["at"])
        ms._remove_benchmark_result("ab-test-m")
        self.assertIsNone(ms._get_benchmark_result("ab-test-m"))

    def test_benchmark_model_records_result(self):
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 128}}
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        target = MagicMock(base_url="http://10.131.1.7:8000")

        async def run():
            with patch.object(ms, "_benchmark_precheck",
                              new=AsyncMock(return_value=(client, {}, "vllm-id"))), \
                 patch.object(ms, "resolve_api_target", return_value=target), \
                 patch.object(config_manager, "get_config",
                              return_value=ServerConfig()):
                return await ms.benchmark_model("ab-test-m")

        result = asyncio.run(run())
        self.assertTrue(result["success"])
        rec = ms._get_benchmark_result("ab-test-m")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["completion_tokens"], 128)
        self.assertIn("+00:00", rec["at"])


class TestAutoBenchTrigger(_AutoBenchBase):
    def test_sync_running_triggers_auto_benchmark(self):
        model_id = "ab-test-model"
        state_machine.add_model(
            model_id=model_id, name=model_id, path="/home/uforce/models/ab-test",
            config={"name": model_id, "path": "/home/uforce/models/ab-test"})
        ms._mark_auto_bench_pending(model_id)

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{"id": model_id}]}
        client = MagicMock()
        client.get = AsyncMock(return_value=response)
        target = MagicMock(base_url="http://10.131.1.7:8000", headers={})

        async def run():
            await ms._sync_status_once(timeout=1.0)
            await asyncio.gather(*list(ms._auto_bench_tasks))

        auto_bench = AsyncMock()
        with patch.object(ms, "_auto_benchmark", auto_bench), \
             patch.object(ms, "get_http_client", return_value=client), \
             patch.object(ms, "resolve_api_target", return_value=target), \
             patch.object(config_manager, "get_config",
                          return_value=ServerConfig()):
            asyncio.run(run())

        self.assertIs(state_machine.get_model(model_id).status,
                      ms.ModelStatus.RUNNING)
        auto_bench.assert_awaited_once_with(model_id)


class TestListModelsWithBenchmark(_AutoBenchBase):
    def test_benchmark_attached_without_mutating_state(self):
        state_machine.add_model(
            model_id="ab-test-with", name="ab-test-with", path="/a/ab-test-with",
            config={"name": "ab-test-with", "path": "/a/ab-test-with"})
        state_machine.add_model(
            model_id="ab-test-without", name="ab-test-without", path="/a/ab-test-without",
            config={"name": "ab-test-without", "path": "/a/ab-test-without"})
        ms._record_benchmark_result("ab-test-with", {
            "tokens_per_second": 50.0, "prompt_tokens": 5,
            "completion_tokens": 64, "elapsed_seconds": 1.28,
        })

        async def run():
            with patch.object(ms, "_sync_status_once", new=AsyncMock()):
                return await ms.list_models()

        models = asyncio.run(run())
        by_id = {m.id: m for m in models}
        self.assertIsNotNone(by_id["ab-test-with"].benchmark)
        self.assertEqual(by_id["ab-test-with"].benchmark.tokens_per_second, 50.0)
        self.assertIsNone(by_id["ab-test-without"].benchmark)
        # state machine objects are not mutated in place
        self.assertIsNone(state_machine.get_model("ab-test-with").benchmark)


if __name__ == "__main__":
    unittest.main()
