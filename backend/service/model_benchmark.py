"""Model benchmarking: single-flight guard, result store, auto-benchmark.

Split out of ``model_service.py`` (file-size gate). Shared state (result
store, pending flags, patch hooks) lives on the ``model_service`` facade and
is resolved through it, so tests patching ``model_service.X`` keep working.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

from ..schemas.model import BenchmarkInfo, ModelInfo
from . import model_service as _ms

logger = logging.getLogger(__name__)

BENCHMARK_PROMPT = ("The quick brown fox jumps over the lazy dog. "
                    "This is a test of the model's generation speed.")
BENCHMARK_MAX_TOKENS = 128

_BENCHMARK_RESULTS_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "benchmark_results.json"
)
_AUTO_BENCH_PENDING_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "auto_bench_pending.json"
)


def _load_persisted_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.warning(f"读取 {path.name} 失败: {e}")
        return default


def _write_persisted_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _persist_persisted(path: Path, data) -> None:
    try:
        _write_persisted_json(path, data)
    except Exception as e:
        logger.error(f"保存 {path.name} 失败: {e}")


def _benchmark_acquire(key: str) -> bool:
    with _ms._benchmark_inflight_lock:
        if key in _ms._benchmark_inflight:
            return False
        _ms._benchmark_inflight.add(key)
        return True


def _benchmark_release(key: str) -> None:
    with _ms._benchmark_inflight_lock:
        _ms._benchmark_inflight.discard(key)


def _record_benchmark_result(model_id: str, result: dict) -> None:
    with _ms._benchmark_store_lock:
        _ms._benchmark_results[model_id] = {
            "tokens_per_second": result["tokens_per_second"],
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"],
            "elapsed_seconds": result["elapsed_seconds"],
            "at": datetime.now(UTC).isoformat(),
        }
        _persist_persisted(_BENCHMARK_RESULTS_FILE, _ms._benchmark_results)


def _remove_benchmark_result(model_id: str) -> None:
    with _ms._benchmark_store_lock:
        if _ms._benchmark_results.pop(model_id, None) is not None:
            _persist_persisted(_BENCHMARK_RESULTS_FILE, _ms._benchmark_results)


def _get_benchmark_result(model_id: str) -> dict | None:
    with _ms._benchmark_store_lock:
        return _ms._benchmark_results.get(model_id)


def _mark_auto_bench_pending(model_id: str) -> None:
    with _ms._benchmark_store_lock:
        _ms._auto_bench_pending.add(model_id)
        _persist_persisted(_AUTO_BENCH_PENDING_FILE, sorted(_ms._auto_bench_pending))


def _take_auto_bench_pending(model_id: str) -> bool:
    """Claim the pending flag for ``model_id`` (once); True if it was set."""
    with _ms._benchmark_store_lock:
        if model_id not in _ms._auto_bench_pending:
            return False
        _ms._auto_bench_pending.discard(model_id)
        _persist_persisted(_AUTO_BENCH_PENDING_FILE, sorted(_ms._auto_bench_pending))
        return True


async def _auto_benchmark(model_id: str) -> None:
    # Two runs like the old front-end auto-bench: the first is a warm-up,
    # the second result is the one kept.
    for run in (1, 2):
        try:
            await _ms.benchmark_model(model_id)
        except HTTPException as e:
            if e.status_code == 409:
                logger.info(f"自动测速跳过 {model_id}：已有测速在进行")
                return
            logger.warning(f"自动测速第 {run} 轮失败 {model_id}: {e.detail}")
        except Exception as e:
            logger.warning(f"自动测速第 {run} 轮失败 {model_id}: {e}")
    logger.info(f"自动测速完成 {model_id}")


def _maybe_trigger_auto_bench(model_id: str) -> None:
    if not _take_auto_bench_pending(model_id):
        return
    task = asyncio.create_task(_ms._auto_benchmark(model_id))
    _ms._auto_bench_tasks.add(task)
    task.add_done_callback(_ms._auto_bench_tasks.discard)


def _with_benchmark(models: list[ModelInfo]) -> list[ModelInfo]:
    out = []
    for m in models:
        r = _get_benchmark_result(m.id)
        if r:
            try:
                m = m.model_copy(update={"benchmark": BenchmarkInfo(**r)})
            except Exception as e:
                # A malformed stored record must not drop the model from the
                # listing — skip the merge, but keep it observable (v7: this
                # used to be a silent except-pass).
                logger.debug(f"Benchmark merge failed for {m.id}: {e}")
        out.append(m)
    return out


async def _benchmark_precheck(model_id: str) -> tuple:
    model = _ms.state_machine.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    config = _ms.config_manager.get_config()
    client = _ms.get_http_client()
    target = _ms.resolve_api_target(config)
    try:
        resp = await client.get(f"{target.base_url}/v1/models", timeout=10.0, headers=target.headers)
        resp.raise_for_status()
        remote_models = resp.json().get("data", [])
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"vLLM API 不可达: {e}")
    if not remote_models:
        raise HTTPException(status_code=400, detail="vLLM 服务没有已加载的模型")

    remote_ids = {m.get("id") for m in remote_models if m.get("id")}
    vllm_model_id = _ms._match_served_id(model, remote_ids)
    if vllm_model_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_id}' is not loaded on vLLM. Loaded models: {sorted(remote_ids)}"
        )
    return client, target.headers, vllm_model_id


async def benchmark_model(model_id: str) -> dict:
    client, auth_headers, vllm_model_id = await _ms._benchmark_precheck(model_id)
    if not _benchmark_acquire(vllm_model_id):
        raise HTTPException(status_code=409,
                             detail=f"A benchmark is already running for model '{vllm_model_id}'")
    try:
        url = f"{_ms.resolve_api_target(_ms.config_manager.get_config()).base_url}/v1/completions"
        timeout = _ms.settings.BENCHMARK_TIMEOUT
        payload = {
            "model": vllm_model_id,
            "prompt": BENCHMARK_PROMPT,
            "max_tokens": BENCHMARK_MAX_TOKENS,
            "temperature": 0,
            "ignore_eos": True,
            "stream": False,
        }
        start = time.perf_counter()
        try:
            response = await client.post(url, json=payload, timeout=timeout, headers=auth_headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                payload = {k: v for k, v in payload.items() if k != "ignore_eos"}
                start = time.perf_counter()
                try:
                    response = await client.post(url, json=payload, timeout=timeout,
                                                 headers=auth_headers)
                    response.raise_for_status()
                except httpx.HTTPError as e2:
                    raise HTTPException(status_code=502,
                                        detail=f"测速请求失败: {e2}") from e2
            else:
                raise HTTPException(status_code=502,
                                    detail=f"测速请求失败: {e}") from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502,
                                detail=f"测速请求失败: {e}") from e
        elapsed = time.perf_counter() - start
        try:
            data = response.json()
        except ValueError as e:
            raise HTTPException(status_code=502,
                                detail=f"vLLM 返回非 JSON 响应: {e}") from e
        usage = data.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        tokens_per_second = (round(completion_tokens / elapsed, 1)
                             if elapsed > 0 and completion_tokens > 0 else 0.0)
        result: dict[str, Any] = {
            "success": True,
            "model_id": model_id,
            "vllm_model_id": vllm_model_id,
            "tokens_per_second": tokens_per_second,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "elapsed_seconds": round(elapsed, 2),
        }
        _record_benchmark_result(model_id, result)
        return result
    finally:
        _benchmark_release(vllm_model_id)
