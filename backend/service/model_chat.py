"""Model chat: SSE streaming proxy to the remote vLLM OpenAI API.

Split per the file-size gate. The dashboard's browser cannot reach the
AI server's vLLM port directly (different network segment, and vLLM
sends no CORS headers), so chat is proxied through the backend on the
same path as the benchmark: ``resolve_api_target`` + the shared httpx
client + auth headers. vLLM's SSE frames (``data: {...}`` chunks and
``data: [DONE]``) pass through verbatim; failures after the stream has
started are surfaced as a single ``data: {"error": ...}`` frame,
because a StreamingResponse cannot switch to an HTTP error code once
it has begun.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import HTTPException

from ..config.api_target import ApiTarget
from . import model_service as _ms

logger = logging.getLogger(__name__)

# Thinking models (launched with --reasoning-parser) share the generation
# budget with their thinking tokens, so the default must fit both.
CHAT_MAX_TOKENS_DEFAULT = 131072


def _error_frame(message: str) -> str:
    return "data: " + json.dumps({"error": {"message": message}}, ensure_ascii=False) + "\n\n"


def _vllm_error_detail(body: bytes) -> str:
    """Extract a human-readable message from a vLLM error body."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body[:300].decode("utf-8", "replace").strip() or "(empty body)"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    if isinstance(err, str):
        return err[:300]
    return str(data)[:300]


async def chat_precheck(model_id: str) -> tuple[httpx.AsyncClient, ApiTarget, str]:
    """Same guards as the benchmark precheck; return (client, target, served id)."""
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
            detail=f"Model '{model_id}' is not loaded on vLLM. Loaded models: {sorted(remote_ids)}",
        )
    # Audit 2026-10-22 BCK-1: /v1/models is static — a zombie engine (core
    # dead, API process alive) still lists the model, but the completion
    # request then dies mid-stream with an obscure error. /health is the
    # engine's own liveness answer; fail fast with a clear 503 instead of
    # a broken SSE stream.
    healthy = await _ms._check_vllm_engine_health(timeout=5.0)
    if healthy is False:
        raise HTTPException(
            status_code=503,
            detail="vLLM 引擎不健康（/health 异常）——引擎可能已崩溃，等待自动恢复或手动重启",
        )
    return client, target, vllm_model_id


async def stream_chat(
    client: httpx.AsyncClient,
    target: ApiTarget,
    vllm_model_id: str,
    messages: list[dict[str, str]],
    max_tokens: int = CHAT_MAX_TOKENS_DEFAULT,
):
    """Yield vLLM SSE frames verbatim; yield an error frame on failure.

    ``stream_options.include_usage`` asks vLLM to put token usage in the
    final chunk (the frontend shows tokens/elapsed/tok/s); older vLLM
    versions simply omit it and the frontend degrades gracefully.
    """
    payload = {
        "model": vllm_model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    url = f"{target.base_url}/v1/chat/completions"
    try:
        # Audit 2026-10-20 P2-10: a bare float would set ALL of httpx's
        # timeouts identically; make the semantics explicit — CHAT_TIMEOUT
        # is the read bound (max gap between two SSE frames), connect/pool
        # use the short API bound.
        async with client.stream("POST", url, json=payload, headers=target.headers,
                                 timeout=httpx.Timeout(
                                     connect=_ms.settings.API_TIMEOUT,
                                     read=_ms.settings.CHAT_TIMEOUT,
                                     write=30.0,
                                     pool=_ms.settings.API_TIMEOUT,
                                 )) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                detail = _vllm_error_detail(body)
                logger.warning("chat: vLLM returned %s: %s", resp.status_code, detail)
                yield _error_frame(f"vLLM 接口错误 {resp.status_code}: {detail}")
                return
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    yield line + "\n\n"
    except asyncio.CancelledError:
        # Shutdown / client-gone race (audit 2026-10-20 P3-15): the
        # consuming task was cancelled mid-stream (e.g. close_http_client
        # at shutdown) — nobody is left to read an error frame, so end
        # quietly instead of raising inside the response body.
        return
    except httpx.HTTPError as e:
        logger.warning("chat: stream to vLLM failed: %s", e)
        yield _error_frame(f"vLLM 接口连接失败: {e}")
