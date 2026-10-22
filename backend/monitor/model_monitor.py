import logging
import time
from typing import Any

import httpx

from ..config.remote_client import config_manager, get_http_client, resolve_api_target
from ..config.server_config import ServerConfig

# file-size gate: the /metrics parsing (metric-name vocabulary + Prometheus
# text parser) lives in vllm_metrics; re-export so existing imports keep
# resolving through this module.
from .vllm_metrics import parse_vllm_metrics as parse_vllm_metrics

logger = logging.getLogger(__name__)

METRICS_FETCH_TIMEOUT = httpx.Timeout(3.0, connect=2.0)

_STALE_INTERVAL = 300.0

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

def _disconnected_payload(timestamp: float | None = None, api_port: int | None = None,
                          api_key: str | None = None) -> dict[str, Any]:
    # Intentional, unmasked: the UI's OpenAI row shows this key so the user
    # can paste it into their own client — it is the user's own remote vLLM
    # key echoed back to the operator of this dashboard.
    return {
        "status": "disconnected",
        "models": [],
        "api_port": api_port,
        "api_key": api_key,
        "running_requests": None,
        "waiting_requests": None,
        "kv_cache_usage_pct": None,
        "generation_tokens_per_s": None,
        "prompt_tokens_per_s": None,
        "generation_tokens_total": None,
        "prompt_tokens_total": None,
        "mtp_hit_rate_pct": None,
        "mtp_hit_rate_cumulative_pct": None,
        "prefix_cache_hit_rate_pct": None,
        "prefix_cache_hit_rate_cumulative_pct": None,
        "avg_ttft_s": None,
        "avg_tpot_ms": None,
        "avg_e2e_latency_s": None,
        "admission_rejections_total": None,
        "num_preemptions_total": None,
        "avg_queue_s": None,
        "avg_prefill_s": None,
        "avg_decode_s": None,
        "timestamp": timestamp if timestamp is not None else time.time(),
    }

def _to_ms(seconds: float | None) -> float | None:
    return round(seconds * 1000.0, 1) if seconds is not None else None

async def fetch_model_status(config: ServerConfig | None = None) -> dict[str, Any]:
    if config is None:
        config = config_manager.get_config()
    target = resolve_api_target(config)
    if not config.host:
        return _disconnected_payload(api_port=target.port, api_key=target.api_key)

    now = time.time()
    client = get_http_client()
    try:
        response = await client.get(
            f"{target.base_url}/metrics",
            timeout=METRICS_FETCH_TIMEOUT,
            headers=target.headers,
        )
        response.raise_for_status()
        parsed = parse_vllm_metrics(response.text)
    except Exception as e:
        logger.debug("Fetch vLLM /metrics failed: %s", e)
        return _disconnected_payload(now, api_port=target.port, api_key=target.api_key)

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

    # "api_key" is intentionally unmasked here too (same reason as the
    # disconnected payload): the UI needs the real key for its OpenAI row.
    return {
        "status": "connected",
        "models": sorted(parsed["models"]),
        "api_port": target.port,
        "api_key": target.api_key,
        "running_requests": parsed["running_requests"],
        "waiting_requests": parsed["waiting_requests"],
        "kv_cache_usage_pct": parsed["kv_cache_usage_pct"],
        "generation_tokens_per_s": (
            round(rates["gen_tokens_total"], 2)
            if rates.get("gen_tokens_total") is not None else None),
        "prompt_tokens_per_s": (
            round(rates["prompt_tokens_total"], 2)
            if rates.get("prompt_tokens_total") is not None else None),
        "generation_tokens_total": (
            round(parsed["gen_tokens_total"])
            if parsed["gen_tokens_total"] is not None else None),
        "prompt_tokens_total": (
            round(parsed["prompt_tokens_total"])
            if parsed["prompt_tokens_total"] is not None else None),
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
        "admission_rejections_total": (
            round(parsed["admission_rejections_total"])
            if parsed["admission_rejections_total"] is not None else None),
        "num_preemptions_total": (
            round(parsed["num_preemptions_total"])
            if parsed["num_preemptions_total"] is not None else None),
        "avg_queue_s": (
            round(parsed["queue_avg_s"], 3)
            if parsed["queue_avg_s"] is not None else None),
        "avg_prefill_s": (
            round(parsed["prefill_avg_s"], 3)
            if parsed["prefill_avg_s"] is not None else None),
        "avg_decode_s": (
            round(parsed["decode_avg_s"], 3)
            if parsed["decode_avg_s"] is not None else None),
        "timestamp": now,
    }
