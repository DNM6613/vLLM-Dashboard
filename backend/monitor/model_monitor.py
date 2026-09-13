import logging
import math
import time
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

from ..config.remote_client import config_manager, get_auth_headers, get_http_client
from ..config.server_config import ServerConfig

logger = logging.getLogger(__name__)

METRICS_FETCH_TIMEOUT = httpx.Timeout(3.0, connect=2.0)

_KV_CACHE_NAMES = ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc")
_TTFT_NAMES = ("vllm:time_to_first_token_seconds",)
_TPOT_NAMES = ("vllm:inter_token_latency_seconds", "vllm:time_per_output_token_seconds")
_E2E_NAMES = ("vllm:e2e_request_latency_seconds", "vllm:time_to_last_token_seconds")
_RUNNING_NAMES = ("vllm:num_requests_running",)
_WAITING_NAMES = ("vllm:num_requests_waiting",)
_PREEMPT_NAMES = ("vllm:num_preemptions_total",)
_GEN_TOKEN_NAMES = ("vllm:generation_tokens_total", "vllm:num_generation_tokens_total")
_PROMPT_TOKEN_NAMES = ("vllm:prompt_tokens_total", "vllm:num_prompt_tokens_total")
_SPEC_DRAFT_TOKEN_NAMES = ("vllm:spec_decode_num_draft_tokens_total",)
_SPEC_ACCEPTED_TOKEN_NAMES = ("vllm:spec_decode_num_accepted_tokens_total",)
_PREFIX_QUERY_TOKEN_NAMES = ("vllm:prefix_cache_queries_total",)
_PREFIX_HIT_TOKEN_NAMES = ("vllm:prefix_cache_hits_total",)

_STALE_INTERVAL = 300.0

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
        "preemptions_total": None,
        "gen_tokens_total": None,
        "prompt_tokens_total": None,
        "spec_draft_tokens_total": None,
        "spec_accepted_tokens_total": None,
        "prefix_cache_queries_total": None,
        "prefix_cache_hits_total": None,
        "ttft_avg_s": None,
        "tpot_avg_s": None,
        "e2e_avg_s": None,
    }
    try:
        families = list(text_string_to_metric_families(text))
    except Exception:
        return result

    hist_acc: dict[str, list[float]] = {
        "ttft_avg_s": [0.0, 0.0],
        "tpot_avg_s": [0.0, 0.0],
        "e2e_avg_s": [0.0, 0.0],
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
        elif _matches(_PREEMPT_NAMES, family_name):
            total = sum(_sample_value(s) for s in samples)
            result["preemptions_total"] = round((result["preemptions_total"] or 0) + total)
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

class ModelStatusTracker:

    def __init__(self) -> None:
        self._prev: dict[str, tuple[float, float]] = {}

    def update(self, counters: dict[str, float], now: float) -> dict[str, float | None]:
        rates: dict[str, float | None] = {}
        for key, value in counters.items():
            prev = self._prev.get(key)
            rate: float | None = None
            if prev is not None:
                dt = now - prev[0]
                if 0 < dt <= _STALE_INTERVAL and value >= prev[1]:
                    rate = (value - prev[1]) / dt
            rates[key] = rate
        for key, value in counters.items():
            self._prev[key] = (now, value)
        return rates

model_status_tracker = ModelStatusTracker()

def _disconnected_payload(timestamp: float | None = None) -> dict[str, Any]:
    return {
        "status": "disconnected",
        "models": [],
        "running_requests": None,
        "waiting_requests": None,
        "kv_cache_usage_pct": None,
        "generation_tokens_per_s": None,
        "prompt_tokens_per_s": None,
        "mtp_hit_rate_pct": None,
        "mtp_hit_rate_cumulative_pct": None,
        "prefix_cache_hit_rate_pct": None,
        "prefix_cache_hit_rate_cumulative_pct": None,
        "avg_ttft_s": None,
        "avg_tpot_ms": None,
        "avg_e2e_latency_s": None,
        "preemptions_total": None,
        "timestamp": timestamp if timestamp is not None else time.time(),
    }

def _to_ms(seconds: float | None) -> float | None:
    return round(seconds * 1000.0, 1) if seconds is not None else None

async def fetch_model_status(config: ServerConfig | None = None) -> dict[str, Any]:
    if config is None:
        config = config_manager.get_config()
    if not config.host:
        return _disconnected_payload()

    now = time.time()
    client = get_http_client()
    headers = get_auth_headers(config)
    try:
        response = await client.get(
            f"{config.get_base_url()}/metrics",
            timeout=METRICS_FETCH_TIMEOUT,
            headers=headers,
        )
        response.raise_for_status()
        parsed = parse_vllm_metrics(response.text)
    except Exception as e:
        logger.debug("Fetch vLLM /metrics failed: %s", e)
        return _disconnected_payload(now)

    counters = {
        key: parsed[key]
        for key in ("gen_tokens_total", "prompt_tokens_total",
                    "spec_draft_tokens_total", "spec_accepted_tokens_total",
                    "prefix_cache_queries_total", "prefix_cache_hits_total")
        if parsed.get(key) is not None
    }
    rates = model_status_tracker.update(counters, now)
    draft_rate = rates.get("spec_draft_tokens_total")
    accept_rate = rates.get("spec_accepted_tokens_total")
    mtp_hit_rate_pct = (
        round(accept_rate / draft_rate * 100.0, 1)
        if (draft_rate and accept_rate is not None) else None)
    draft_total = counters.get("spec_draft_tokens_total")
    accept_total = counters.get("spec_accepted_tokens_total")
    mtp_hit_rate_cumulative_pct = (
        round(accept_total / draft_total * 100.0, 1)
        if (draft_total and accept_total is not None) else None)
    query_rate = rates.get("prefix_cache_queries_total")
    hit_rate = rates.get("prefix_cache_hits_total")
    prefix_cache_hit_rate_pct = (
        round(hit_rate / query_rate * 100.0, 1)
        if (query_rate and hit_rate is not None) else None)
    query_total = counters.get("prefix_cache_queries_total")
    hit_total = counters.get("prefix_cache_hits_total")
    prefix_cache_hit_rate_cumulative_pct = (
        round(hit_total / query_total * 100.0, 1)
        if (query_total and hit_total is not None) else None)

    return {
        "status": "connected",
        "models": sorted(parsed["models"]),
        "running_requests": parsed["running_requests"],
        "waiting_requests": parsed["waiting_requests"],
        "kv_cache_usage_pct": parsed["kv_cache_usage_pct"],
        "generation_tokens_per_s": (
            round(rates["gen_tokens_total"], 2)
            if rates.get("gen_tokens_total") is not None else None),
        "prompt_tokens_per_s": (
            round(rates["prompt_tokens_total"], 2)
            if rates.get("prompt_tokens_total") is not None else None),
        "mtp_hit_rate_pct": mtp_hit_rate_pct,
        "mtp_hit_rate_cumulative_pct": mtp_hit_rate_cumulative_pct,
        "prefix_cache_hit_rate_pct": prefix_cache_hit_rate_pct,
        "prefix_cache_hit_rate_cumulative_pct": prefix_cache_hit_rate_cumulative_pct,
        "avg_ttft_s": (
            round(parsed["ttft_avg_s"], 3)
            if parsed["ttft_avg_s"] is not None else None),
        "avg_tpot_ms": _to_ms(parsed["tpot_avg_s"]),
        "avg_e2e_latency_s": (
            round(parsed["e2e_avg_s"], 3)
            if parsed["e2e_avg_s"] is not None else None),
        "preemptions_total": parsed["preemptions_total"],
        "timestamp": now,
    }
