"""vLLM /metrics parsing (backend/monitor/vllm_metrics.py).

Covers the legacy metric vocabulary the dashboard has always parsed plus
the vLLM >= 0.32.0 observability additions (admission rejections,
scheduler preemptions, per-request queue/prefill/decode latency). Older
vLLM versions do not export the new families — the parser must return None
for them and keep the legacy fields correct.
"""

import unittest

from backend.monitor.vllm_metrics import parse_vllm_metrics

# A realistic slice of a vLLM >= 0.32.0 /metrics body: the legacy metrics
# plus the 0.32.0 additions. Histogram sums/counts are chosen so the
# averages are exact (queue 25/10=2.5, prefill 40/10=4.0,
# decode 220/10=22.0).
METRICS_0320 = """
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="m"} 3.0
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting{model_name="m"} 1.0
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc{model_name="m"} 0.42
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total{model_name="m"} 1000.0
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total{model_name="m"} 500.0
# TYPE vllm:spec_decode_num_draft_tokens_total counter
vllm:spec_decode_num_draft_tokens_total{model_name="m"} 400.0
# TYPE vllm:spec_decode_num_accepted_tokens_total counter
vllm:spec_decode_num_accepted_tokens_total{model_name="m"} 280.0
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{model_name="m"} 50.0
# TYPE vllm:prefix_cache_hits_total counter
vllm:prefix_cache_hits_total{model_name="m"} 35.0
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_sum{model_name="m"} 5.0
vllm:time_to_first_token_seconds_count{model_name="m"} 10.0
# TYPE vllm:inter_token_latency_seconds histogram
vllm:inter_token_latency_seconds_sum{model_name="m"} 2.0
vllm:inter_token_latency_seconds_count{model_name="m"} 100.0
# TYPE vllm:e2e_request_latency_seconds histogram
vllm:e2e_request_latency_seconds_sum{model_name="m"} 90.0
vllm:e2e_request_latency_seconds_count{model_name="m"} 10.0
# TYPE vllm:admission_rejections_total counter
vllm:admission_rejections_total{model_name="m"} 7.0
# TYPE vllm:num_preemptions_total counter
vllm:num_preemptions_total{model_name="m"} 13.0
# TYPE vllm:request_queue_time_seconds histogram
vllm:request_queue_time_seconds_sum{model_name="m"} 25.0
vllm:request_queue_time_seconds_count{model_name="m"} 10.0
# TYPE vllm:request_prefill_time_seconds histogram
vllm:request_prefill_time_seconds_sum{model_name="m"} 40.0
vllm:request_prefill_time_seconds_count{model_name="m"} 10.0
# TYPE vllm:request_decode_time_seconds histogram
vllm:request_decode_time_seconds_sum{model_name="m"} 220.0
vllm:request_decode_time_seconds_count{model_name="m"} 10.0
"""

# Same body minus the 0.32.0-only families: an older vLLM version.
METRICS_LEGACY = METRICS_0320.split("# TYPE vllm:admission_rejections_total")[0]


class TestParseVllmMetrics(unittest.TestCase):
    def test_parses_0320_observability_metrics(self):
        parsed = parse_vllm_metrics(METRICS_0320)
        self.assertEqual(parsed["admission_rejections_total"], 7.0)
        self.assertEqual(parsed["num_preemptions_total"], 13.0)
        self.assertAlmostEqual(parsed["queue_avg_s"], 2.5)
        self.assertAlmostEqual(parsed["prefill_avg_s"], 4.0)
        self.assertAlmostEqual(parsed["decode_avg_s"], 22.0)

    def test_parses_legacy_metrics(self):
        parsed = parse_vllm_metrics(METRICS_0320)
        self.assertEqual(parsed["running_requests"], 3)
        self.assertEqual(parsed["waiting_requests"], 1)
        self.assertEqual(parsed["kv_cache_usage_pct"], 42.0)
        self.assertEqual(parsed["gen_tokens_total"], 1000.0)
        self.assertEqual(parsed["prompt_tokens_total"], 500.0)
        self.assertEqual(parsed["spec_draft_tokens_total"], 400.0)
        self.assertEqual(parsed["spec_accepted_tokens_total"], 280.0)
        self.assertEqual(parsed["prefix_cache_queries_total"], 50.0)
        self.assertEqual(parsed["prefix_cache_hits_total"], 35.0)
        self.assertAlmostEqual(parsed["ttft_avg_s"], 0.5)
        self.assertAlmostEqual(parsed["tpot_avg_s"], 0.02)
        self.assertAlmostEqual(parsed["e2e_avg_s"], 9.0)
        self.assertEqual(parsed["models"], {"m"})

    def test_older_vllm_without_new_metrics(self):
        parsed = parse_vllm_metrics(METRICS_LEGACY)
        self.assertIsNone(parsed["admission_rejections_total"])
        self.assertIsNone(parsed["num_preemptions_total"])
        self.assertIsNone(parsed["queue_avg_s"])
        self.assertIsNone(parsed["prefill_avg_s"])
        self.assertIsNone(parsed["decode_avg_s"])
        # legacy fields stay correct
        self.assertEqual(parsed["running_requests"], 3)
        self.assertAlmostEqual(parsed["ttft_avg_s"], 0.5)

    def test_malformed_text_returns_all_none(self):
        parsed = parse_vllm_metrics("garbage {{{ not prometheus")
        self.assertIsNone(parsed["running_requests"])
        self.assertIsNone(parsed["admission_rejections_total"])
        self.assertIsNone(parsed["queue_avg_s"])


if __name__ == "__main__":
    unittest.main()
