"""Model status sync: reconcile local state with the remote vLLM API.

Split out of ``model_service.py`` (file-size gate). Shared state and patch
hooks live on the ``model_service`` facade and are resolved through it.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime

from ..executor.remote.process_ops import _VLLM_MAIN_PATTERN, _ps_filter_self
from ..schemas.model import ModelInfo, ModelStatus
from . import model_service as _ms
from .model_service import LOADING_GRACE_SECONDS, LOADING_HARD_CAP_SECONDS

logger = logging.getLogger(__name__)


def _loading_reset_allowed(model: ModelInfo, now: datetime | None = None,
                           grace_seconds: float = LOADING_GRACE_SECONDS) -> bool:
    if model.loading_since is None:
        return True
    if now is None:
        now = datetime.now()
    return (now - model.loading_since).total_seconds() >= grace_seconds


def _loading_hard_reset_allowed(model: ModelInfo, now: datetime | None = None,
                                hard_cap_seconds: float = LOADING_HARD_CAP_SECONDS) -> bool:
    if model.loading_since is None:
        return False
    if now is None:
        now = datetime.now()
    return (now - model.loading_since).total_seconds() >= hard_cap_seconds


async def _check_vllm_present_strict(api_host: str, api_port: int) -> bool | None:
    probe_host = api_host if api_host not in ("", "localhost") else "127.0.0.1"
    try:
        probe_sock = await asyncio.to_thread(
            socket.create_connection, (probe_host, api_port), _ms._PROBE_TCP_TIMEOUT
        )
        probe_sock.close()
        return True
    except OSError:
        pass
    try:
        executor = _ms.get_remote_executor()
        def _probe_ps():
            return executor.execute(_ps_filter_self(_VLLM_MAIN_PATTERN))
        result = await asyncio.to_thread(_probe_ps)
        if not result["success"]:
            return None
        return bool(result["stdout"].strip())
    except Exception as e:
        logger.warning(f"vLLM presence probe failed (treating as unknown): {e}")
        return None


async def _sync_status_once(timeout: float, track_current: bool = False,
                            warn_unreachable: bool = False) -> int:
    config = _ms.config_manager.get_config()
    client = _ms.get_http_client()
    target = _ms.resolve_api_target(config)
    try:
        response = await client.get(
            f"{target.base_url}/v1/models",
            timeout=timeout,
            headers=target.headers,
        )
        response.raise_for_status()
        remote_models = response.json().get("data", [])
    except Exception as e:
        remote_models = []
        if warn_unreachable:
            logger.warning(f"Remote vLLM API unreachable, model status may be stale: {e}")
    remote_model_ids = {m.get('id') for m in remote_models if m.get('id')}
    models = _ms.state_machine.get_all_models()

    has_loading = any(m.status == ModelStatus.LOADING for m in models)
    has_running = any(m.status == ModelStatus.RUNNING for m in models)
    vllm_process_exists = True
    strict_present: bool | None = None
    if (has_loading or has_running) and not remote_model_ids:
        strict_present = await _check_vllm_present_strict(config.host, target.port)
        vllm_process_exists = (strict_present is True)

    changed = 0
    for model in models:
        is_running = _match_model_name(model, remote_model_ids)

        if model.status in (ModelStatus.LOADING, ModelStatus.DOWNLOADED, ModelStatus.FAILED):
            if is_running:
                _ms.state_machine.update_model_status(model.id, ModelStatus.RUNNING)
                if track_current:
                    _ms.state_machine.set_current_model(model.id)
                changed += 1
                _ms._maybe_trigger_auto_bench(model.id)
            elif model.status == ModelStatus.LOADING and not vllm_process_exists:
                if _loading_reset_allowed(model):
                    _ms.state_machine.update_model_status(model.id, ModelStatus.STOPPED)
                    _ms.state_machine.clear_current_model(model.id)
                    if model.locked:
                        _ms._watchdog_on_crash(model.id)
                    changed += 1
            elif (model.status == ModelStatus.LOADING
                  and _loading_hard_reset_allowed(model)):
                logger.warning(
                    f"Model {model.id} stuck in LOADING for "
                    f">{_ms.LOADING_HARD_CAP_SECONDS // 60} min with a vLLM "
                    f"process present but never serving — resetting to "
                    f"STOPPED (possible port conflict with external vLLM)")
                _ms.state_machine.update_model_status(model.id, ModelStatus.STOPPED)
                _ms.state_machine.clear_current_model(model.id)
                changed += 1
        elif model.status == ModelStatus.RUNNING:
            # Only drop RUNNING when there is positive evidence the model is
            # gone: the API returned a non-empty model list that lacks this
            # model, or the process probe found no vLLM process. A transient
            # empty list (API reachable but returning no models) is not
            # sufficient — that used to flip RUNNING models to STOPPED.
            if not is_running and (remote_model_ids or strict_present is False):
                _ms.state_machine.update_model_status(model.id, ModelStatus.STOPPED)
                _ms.state_machine.clear_current_model(model.id)
                if model.locked:
                    _ms._watchdog_on_crash(model.id)
                changed += 1
        elif model.status == ModelStatus.STOPPED:
            if is_running:
                _ms.state_machine.update_model_status(model.id, ModelStatus.RUNNING)
                if track_current:
                    _ms.state_machine.set_current_model(model.id)
                changed += 1
                _ms._maybe_trigger_auto_bench(model.id)
    return changed


def _match_served_id(model: ModelInfo, remote_ids: set[str]) -> str | None:
    model_dir_name = model.path.split("/")[-1] if "/" in model.path else model.path

    if model.path in remote_ids:
        return model.path
    if model.name in remote_ids:
        return model.name

    normalized_map: dict[str, str] = {}
    for rid in remote_ids:
        normalized_map.setdefault(rid.replace(".", "_").replace("-", "_"), rid)
    normalized_local = model_dir_name.replace(".", "_").replace("-", "_")
    if normalized_local in normalized_map:
        return normalized_map[normalized_local]

    if len(model_dir_name) >= 6:
        for rid in remote_ids:
            if model.path.endswith(rid) or rid.endswith(model_dir_name):
                return rid

    return None


def _match_model_name(model: ModelInfo, remote_ids: set[str]) -> bool:
    return _match_served_id(model, remote_ids) is not None


async def list_models() -> list[ModelInfo]:
    try:
        await _ms._sync_status_once(timeout=3.0, warn_unreachable=True)
        models = _ms.state_machine.get_all_models()
    except Exception as e:
        logger.warning(f"Auto status sync failed in list_models: {e}")
        models = _ms.state_machine.get_all_models()
    return _ms._with_benchmark(models)


async def sync_status() -> int:
    return await _sync_status_once(
        timeout=_ms.settings.STATUS_SYNC_TIMEOUT, track_current=True
    )
