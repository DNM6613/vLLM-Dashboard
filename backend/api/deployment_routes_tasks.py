"""Deployment API — task center, state, mirrors, rollback routes.

Split out of ``api/deployment.py`` (file-size gate). The parent router with
the ``/api/v1/deployment`` prefix lives in ``api/deployment.py`` and includes
this sub-router; all route paths are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..service import deployment_service as ds
from ..service.deployment_service import DeploymentError
from . import deployment as _dep
from .deployment_common import _error_to_http, _probe_in_thread, _require_remote

logger = logging.getLogger(__name__)

router = APIRouter(tags=["deployment"])

# Reboot-verification cooldown. The home page polls /tasks every 3 s; while a
# driver task awaits reboot the target server is usually down, so probing it
# on every tick would pile 15 s SSH attempts onto an unreachable host.
_RECONCILE_COOLDOWN_SECONDS = 30.0
_last_driver_reconcile = 0.0


async def _maybe_reconcile_driver() -> None:
    """Run the post-reboot driver verification from the task-center poll.

    The task center lives on the home page, but the verification used to be
    triggered only by GET /deployment/drivers — polled solely while the
    deploy modal is open. Without this hook a finished reboot would leave
    the task stuck at ``awaiting_reboot`` on the home page until someone
    happened to open the deploy window.
    """
    global _last_driver_reconcile
    if ds.task_manager.pending_driver_task() is None:
        return
    now = time.monotonic()
    if now - _last_driver_reconcile < _RECONCILE_COOLDOWN_SECONDS:
        return
    _last_driver_reconcile = now
    try:
        await asyncio.to_thread(ds.reconcile_driver_task)
    except Exception:
        logger.exception("driver task reconcile from /tasks failed")


# ===========================================================================
# Task center
# ===========================================================================

@router.get("/tasks")
async def list_tasks(limit: int = Query(default=30, ge=1, le=100)):
    await _maybe_reconcile_driver()
    return {"tasks": [t.to_dict() for t in ds.task_manager.list(limit)]}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = ds.task_manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    data = task.to_dict()
    data["log_tail"] = ds.task_manager.tail(task_id, 200)
    return data


@router.get("/tasks/{task_id}/log")
async def get_task_log(task_id: str, offset: int = Query(default=0, ge=0)):
    task = ds.task_manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    content, new_offset = ds.task_manager.read_log(task_id, offset)
    return {"content": content, "offset": new_offset}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    task = ds.task_manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not ds.task_manager.cancel(task_id):
        raise HTTPException(status_code=409, detail="Task is not cancellable in its current state")
    return {"status": "cancelling", "task_id": task_id}


# ===========================================================================
# State / mirrors / rollback
# ===========================================================================

@router.get("/state")
async def get_state():
    state = ds.deployment_state.get()
    executor = _dep.get_remote_executor()
    info = await _probe_in_thread(executor.get_driver_list, 45)
    current_major = ds._major_driver_version(info.get("current_driver", ""))
    pending = ds.task_manager.pending_driver_task()
    cuda_unlocked = current_major is not None
    return {
        **state,
        "current_driver_major": current_major,
        "pending_driver_task": (
            {"id": pending.id, "package": pending.result.get("package", ""), "status": pending.status}
            if pending else None
        ),
        "locks": {
            "cuda": not cuda_unlocked,
        },
    }


@router.put("/state")
async def put_state(body: dict[str, Any]):
    mirrors = body.get("mirrors")
    if isinstance(mirrors, dict):
        for key in ("pypi", "hf"):
            if key in mirrors:
                value = str(mirrors[key] or "").strip()
                if value and not ds.URL_RE.fullmatch(value):
                    raise HTTPException(status_code=400, detail=f"Invalid mirror URL for {key}")
        ds.deployment_state.update_section("mirrors", **{k: str(v or "") for k, v in mirrors.items() if k in ("pypi", "hf")})
    selected = body.get("selected")
    if isinstance(selected, dict):
        allowed_selected = {
            "driver": str, "cuda": str, "cuda_install_system": bool,
            "vllm_version": str, "vllm_runtime": str,
        }
        def _to_bool(key: str, raw: Any) -> bool:
            # bool("false") is True — parse explicitly instead.
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.strip().lower() in ("true", "1"):
                return True
            if isinstance(raw, str) and raw.strip().lower() in ("false", "0"):
                return False
            raise HTTPException(status_code=400, detail=f"Invalid boolean for {key}")

        values = {}
        for key, conv in allowed_selected.items():
            if key in selected:
                values[key] = _to_bool(key, selected[key]) if conv is bool else conv(selected[key])
        if values.get("vllm_runtime") and values["vllm_runtime"] not in ("builtin", "system"):
            raise HTTPException(status_code=400, detail="Invalid vllm_runtime")
        if values:
            ds.deployment_state.update_section("selected", **values)
    return ds.deployment_state.get()


@router.post("/rollback")
async def rollback(body: dict[str, Any]):
    _require_remote()
    target = str(body.get("target") or "")
    try:
        if target == "driver":
            task = await asyncio.to_thread(ds.start_rollback_driver)
        elif target == "vllm":
            task = await asyncio.to_thread(ds.start_rollback_vllm)
        else:
            raise DeploymentError("Rollback target must be 'driver' or 'vllm'")
    except DeploymentError as e:
        raise _error_to_http(e) from e
    return {"status": "started", "task_id": task.id}
