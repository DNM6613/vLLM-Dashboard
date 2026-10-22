"""Deployment API — state, mirrors, rollback routes.

Split out of ``api/deployment.py`` (file-size gate). The parent router with
the ``/api/v1/deployment`` prefix lives in ``api/deployment.py`` and includes
this sub-router; all route paths are unchanged.

Audit 2026-10-20 P3-8 (user ruling): the task-center endpoints (GET /tasks,
GET /tasks/{id}, GET /tasks/{id}/log, POST /tasks/{id}/cancel) had no UI
caller since the task center was removed from the home page — deleted.
In-flight task state is still surfaced via GET /state (active_task /
pending_driver_task), which the Driver tab renders.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from ..service import deployment_service as ds
from ..service.deployment_service import DeploymentError
from . import deployment as _dep
from .deployment_common import _error_to_http, _probe_in_thread, _require_remote

router = APIRouter(tags=["deployment"])


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


# Audit 2026-10-22 SEC-1: these fields are stored verbatim in the deployment
# state file and re-embedded into remote shell commands (mirror URLs, pip /
# uv index flags, venv names). Cap them so a pathological client cannot
# bloat the persisted state or the command lines they flow into.
_MIRROR_URL_MAX = 300
_SELECTED_STR_MAX = 512


@router.put("/state")
async def put_state(body: dict[str, Any]):
    mirrors = body.get("mirrors")
    if isinstance(mirrors, dict):
        for key in ("pypi", "hf"):
            if key in mirrors:
                value = str(mirrors[key] or "").strip()
                if len(value) > _MIRROR_URL_MAX:
                    raise HTTPException(status_code=400, detail=f"Mirror URL too long for {key}")
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
                value = _to_bool(key, selected[key]) if conv is bool else conv(selected[key])
                if conv is str and len(value) > _SELECTED_STR_MAX:
                    raise HTTPException(status_code=400, detail=f"Value too long for {key}")
                values[key] = value
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
