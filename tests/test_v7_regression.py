"""v7 regression: benchmark result merge failure must be observable.

The ruff SIM105 finding at model_service._with_benchmark (formerly line 383)
was a silent ``except Exception: pass`` — a malformed persisted benchmark
record would quietly drop the benchmark from the model listing with no trace.
Locked behavior:

* a corrupt record must not raise and must not drop the model
* the merge failure must be logged (observable, not silent)
* a valid record still merges cleanly
"""
import unittest

from backend.controller.state_machine import state_machine
from backend.service import model_service as ms

_BENCHMARK_LOGGER = "backend.service.model_benchmark"

_VALID_RECORD = {
    "tokens_per_second": 42.5,
    "prompt_tokens": 8,
    "completion_tokens": 64,
    "elapsed_seconds": 1.5,
    "at": "2026-01-01T00:00:00+00:00",
}


class TestBenchmarkMergeObservability(unittest.TestCase):
    def setUp(self):
        self._backup = dict(ms._benchmark_results)
        state_machine.add_model(
            model_id="v7-merge-model", name="v7-merge-model",
            path="/home/uforce/models/v7-merge-model",
            config={"name": "v7-merge-model", "path": "/home/uforce/models/v7-merge-model"})

    def tearDown(self):
        state_machine.remove_model("v7-merge-model")
        ms._benchmark_results.clear()
        ms._benchmark_results.update(self._backup)

    def test_corrupt_record_does_not_drop_model_and_is_logged(self):
        # Missing required BenchmarkInfo fields -> merge raises ValidationError
        ms._benchmark_results["v7-merge-model"] = {"at": "2026-01-01T00:00:00+00:00"}
        model = state_machine.get_model("v7-merge-model")
        with self.assertLogs(_BENCHMARK_LOGGER, level="DEBUG") as cm:
            out = ms._with_benchmark([model])
        # model still listed, benchmark simply absent
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].id, "v7-merge-model")
        self.assertIsNone(out[0].benchmark)
        # the failure is observable, not swallowed (v7: silent except-pass)
        self.assertTrue(
            any("v7-merge-model" in r.getMessage() for r in cm.records),
            "benchmark merge failure was not logged")

    def test_valid_record_still_merges(self):
        ms._benchmark_results["v7-merge-model"] = dict(_VALID_RECORD)
        model = state_machine.get_model("v7-merge-model")
        out = ms._with_benchmark([model])
        self.assertIsNotNone(out[0].benchmark)
        self.assertEqual(out[0].benchmark.tokens_per_second, 42.5)
        self.assertEqual(out[0].benchmark.completion_tokens, 64)
        # the state machine object is not mutated in place
        self.assertIsNone(state_machine.get_model("v7-merge-model").benchmark)

    def test_model_without_record_passes_through_untouched(self):
        model = state_machine.get_model("v7-merge-model")
        out = ms._with_benchmark([model])
        self.assertEqual(len(out), 1)
        self.assertIs(out[0], model)
        self.assertIsNone(out[0].benchmark)


if __name__ == "__main__":
    unittest.main()
