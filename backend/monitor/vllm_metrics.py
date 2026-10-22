"""vLLM /metrics parsing: metric-name vocabulary + Prometheus text parser.

Split out of ``model_monitor.py`` (file-size gate). ``model_monitor`` keeps
the HTTP fetch, the rate tracker, and the payload assembly, and re-exports
``parse_vllm_metrics`` so existing imports keep working.

Metric-name vocabulary: vLLM renamed several metrics across versions
(``kv_cache_usage_perc`` <-> ``gpu_cache_usage_perc``,
``inter_token_latency_seconds`` <-> ``time_per_output_token_seconds``,
``*_total`` aliases), so each logical value lists every known spelling and
:func:`_matches` accepts any of them.
"""

from __future__ import annotations

import math
from typing import Any

from prometheus_client.parser import text_string_to_metric_families

_KV_CACHE_NAMES = ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc")
_TTFT_NAMES = ("vllm:time_to_first_token_seconds",)
_TPOT_NAMES = ("vllm:inter_token_latency_seconds", "vllm:time_per_output_token_seconds")
_E2E_NAMES = ("vllm:e2e_request_latency_seconds", "vllm:time_to_last_token_seconds")
_RUNNING_NAMES = ("vllm:num_requests_running",)
_WAITING_NAMES = ("vllm:num_requests_waiting",)
_GEN_TOKEN_NAMES = ("vllm:generation_tokens_total", "vllm:num_generation_tokens_total")
_PROMPT_TOKEN_NAMES = ("vllm:prompt_tokens_total", "vllm:num_prompt_tokens_total")
_SPEC_DRAFT_TOKEN_NAMES = ("vllm:spec_decode_num_draft_tokens_total",)
_SPEC_ACCEPTED_TOKEN_NAMES = ("vllm:spec_decode_num_accepted_tokens_total",)
_PREFIX_QUERY_TOKEN_NAMES = ("vllm:prefix_cache_queries_total",)
_PREFIX_HIT_TOKEN_NAMES = ("vllm:prefix_cache_hits_total",)
# vLLM >= 0.32.0 observability additions (release notes, "Engine Core ->
# Observability"): admission backpressure, scheduler preemptions, and the
# per-request queue / prefill / decode latency breakdown. Older versions do
# not export them — the parser returns None and the UI degrades to N/A.
_ADMISSION_REJECTIONS_NAMES = ("vllm:admission_rejections_total",)
_PREEMPTIONS_NAMES = ("vllm:num_preemptions_total",)
_QUEUE_TIME_NAMES = ("vllm:request_queue_time_seconds",)
_PREFILL_TIME_NAMES = ("vllm:request_prefill_time_seconds",)
_DECODE_TIME_NAMES = ("vllm:request_decode_time_seconds",)

def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)

def _sample_value(sample: Any) -> float:
    v = sample.value
    return v if _finite(v) else 0.0

def _histogram_sums(samples: list[Any]) -> tuple[float, float]:
    s = 0.0
    c = 0.0
    for sample in samples:
        if sample.name.endswith("_sum"):
            s += _sample_value(sample)
        elif sample.name.endswith("_count"):
            c += _sample_value(sample)
    return s, c

def _matches(candidates: tuple[str, ...], family_name: str) -> bool:
    return family_name in candidates or f"{family_name}_total" in candidates

def parse_vllm_metrics(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "models": set(),
        "running_requests": None,
        "waiting_requests": None,
        "kv_cache_usage_pct": None,
        "gen_tokens_total": None,
        "prompt_tokens_total": None,
        "spec_draft_tokens_total": None,
        "spec_accepted_tokens_total": None,
        "prefix_cache_queries_total": None,
        "prefix_cache_hits_total": None,
        "admission_rejections_total": None,
        "num_preemptions_total": None,
        "ttft_avg_s": None,
        "tpot_avg_s": None,
        "e2e_avg_s": None,
        "queue_avg_s": None,
        "prefill_avg_s": None,
        "decode_avg_s": None,
    }
    try:
        families = list(text_string_to_metric_families(text))
    except Exception:
        return result

    hist_acc: dict[str, list[float]] = {
        "ttft_avg_s": [0.0, 0.0],
        "tpot_avg_s": [0.0, 0.0],
        "e2e_avg_s": [0.0, 0.0],
        "queue_avg_s": [0.0, 0.0],
        "prefill_avg_s": [0.0, 0.0],
        "decode_avg_s": [0.0, 0.0],
    }

    for family in families:
        family_name = family.name
        samples = family.samples
        if _matches(_RUNNING_NAMES, family_name) or _matches(_WAITING_NAMES, family_name):
            key = ("running_requests" if _matches(_RUNNING_NAMES, family_name)
                   else "waiting_requests")
            total = sum(_sample_value(s) for s in samples)
            result[key] = round((result[key] or 0) + total)
        elif _matches(_KV_CACHE_NAMES, family_name):
            values = [_sample_value(s) for s in samples]
            if values:
                pct = max(values) * 100.0
                cand = round(min(100.0, max(0.0, pct)), 1)
                existing = result["kv_cache_usage_pct"]
                result["kv_cache_usage_pct"] = (
                    cand if existing is None else max(existing, cand))
        elif _matches(_GEN_TOKEN_NAMES, family_name) or _matches(_PROMPT_TOKEN_NAMES, family_name):
            key = ("gen_tokens_total" if _matches(_GEN_TOKEN_NAMES, family_name)
                   else "prompt_tokens_total")
            total = sum(_sample_value(s) for s in samples)
            result[key] = (result[key] or 0) + total
        elif _matches(_SPEC_DRAFT_TOKEN_NAMES, family_name) \
                or _matches(_SPEC_ACCEPTED_TOKEN_NAMES, family_name):
            key = ("spec_draft_tokens_total" if _matches(_SPEC_DRAFT_TOKEN_NAMES, family_name)
                   else "spec_accepted_tokens_total")
            total = sum(_sample_value(s) for s in samples)
            result[key] = (result[key] or 0) + total
        elif _matches(_PREFIX_QUERY_TOKEN_NAMES, family_name) \
                or _matches(_PREFIX_HIT_TOKEN_NAMES, family_name):
            key = ("prefix_cache_queries_total" if _matches(_PREFIX_QUERY_TOKEN_NAMES, family_name)
                   else "prefix_cache_hits_total")
            total = sum(_sample_value(s) for s in samples)
            result[key] = (result[key] or 0) + total
        elif _matches(_ADMISSION_REJECTIONS_NAMES, family_name) \
                or _matches(_PREEMPTIONS_NAMES, family_name):
            key = ("admission_rejections_total" if _matches(_ADMISSION_REJECTIONS_NAMES, family_name)
                   else "num_preemptions_total")
            total = sum(_sample_value(s) for s in samples)
            result[key] = (result[key] or 0) + total
        elif _matches(_QUEUE_TIME_NAMES, family_name) \
                or _matches(_PREFILL_TIME_NAMES, family_name) \
                or _matches(_DECODE_TIME_NAMES, family_name):
            key = ("queue_avg_s" if _matches(_QUEUE_TIME_NAMES, family_name)
                   else "prefill_avg_s" if _matches(_PREFILL_TIME_NAMES, family_name)
                   else "decode_avg_s")
            s, c = _histogram_sums(samples)
            acc = hist_acc[key]
            acc[0] += s
            acc[1] += c
        elif _matches(_TTFT_NAMES, family_name) or _matches(_TPOT_NAMES, family_name) \
                or _matches(_E2E_NAMES, family_name):
            key = ("ttft_avg_s" if _matches(_TTFT_NAMES, family_name)
                   else "tpot_avg_s" if _matches(_TPOT_NAMES, family_name)
                   else "e2e_avg_s")
            s, c = _histogram_sums(samples)
            acc = hist_acc[key]
            acc[0] += s
            acc[1] += c

        for s in samples:
            model_name = s.labels.get("model_name")
            if model_name:
                result["models"].add(model_name)

    for key, (s, c) in hist_acc.items():
        if c > 0:
            result[key] = s / c

    return result
