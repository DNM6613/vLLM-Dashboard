"""Deployment API — preflight / driver / CUDA / vLLM routes.

Split out of ``api/deployment.py`` (file-size gate). The parent router with
the ``/api/v1/deployment`` prefix lives in ``api/deployment.py`` and includes
this sub-router; all route paths are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from ..executor.remote.deployment_ops import CUDA_VERSION_PROBE
from ..service import deployment_service as ds
from ..service.deployment_service import (
    DRIVER_MIN_CUDA,
    VLLM_MIN_DRIVER,
    DeploymentError,
)
from . import deployment as _dep
from .deployment_common import (
    _CUDA_VERSIONS,
    _disk_free_gb,
    _driver_sort_key,
    _error_to_http,
    _probe_in_thread,
    _require_remote,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["deployment"])


@router.get("/preflight")
async def get_preflight():
    _require_remote()
    executor = _dep.get_remote_executor()
    result = await _probe_in_thread(executor.get_preflight, 75)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error") or "Preflight failed")

    items: list[dict[str, Any]] = []

    def add(key: str, label: str, status: str, value: str = "", detail: str = "") -> None:
        items.append({"key": key, "label": label, "status": status, "value": value, "detail": detail})

    add("os", "OS", "ok" if result.get("os") else "fail", result.get("os") or "unknown")
    add("kernel", "Kernel", "ok" if result.get("kernel") else "fail",
        result.get("kernel") or "unknown",
        "Open kernel modules need a recent kernel; on Blackwell GPUs the open kernel module is the only supported one."
        if not result.get("kernel") else "")

    if result.get("gpu_missing"):
        add("gpu", "GPU driver", "warn", "driver not installed",
            "No working NVIDIA driver detected — apply one in the GPU Driver tab.")
    else:
        gpus = result.get("gpus") or []
        names = ", ".join(dict.fromkeys(g.get("name", "") for g in gpus if g.get("name")))
        driver = gpus[0].get("driver", "") if gpus else ""
        major = ds._major_driver_version(driver)
        status = "ok"
        detail = ""
        if major is not None and major < VLLM_MIN_DRIVER:
            status = "warn"
            detail = f"Below the vLLM recommended minimum driver {VLLM_MIN_DRIVER}."
        add("gpu", "GPU driver", status,
            f"{names or 'NVIDIA GPU'} · driver {driver or 'unknown'}", detail)

    add("cuda", "CUDA toolkit", "ok",
        result.get("cuda") or "not installed (optional — vLLM ships its own runtime)")

    python = (result.get("python") or "").strip()
    py_major = None
    if python:
        m = re.match(r"^(\d+)\.(\d+)", python)
        if m:
            py_major = (int(m.group(1)), int(m.group(2)))
    # vLLM 0.29.0 requires_python: >=3.10,<3.15 (PyPI metadata).
    py_ok = bool(py_major) and (3, 10) <= py_major < (3, 15)
    add("python", "Python", "ok" if py_ok else "fail", python or "not found",
        "" if py_ok else "Python 3.10+ (<3.15) is required for vLLM.")

    uv_ok = not result.get("uv_missing", False)
    add("uv", "uv", "ok" if uv_ok else "fail", result.get("uv") or "not found",
        "" if uv_ok else "Install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh")

    free_gb = _disk_free_gb(result.get("disk") or [])
    disk_ok = free_gb is not None and free_gb >= 5
    add("disk", "Disk", "ok" if disk_ok else "fail",
        f"{free_gb:.1f} GB free" if free_gb is not None else "unknown",
        "" if disk_ok else "5 GB free space is the minimum for driver/CUDA/vLLM packages.")

    tool_ok = result.get("driver_tool", False)
    add("driver_tool", "ubuntu-drivers", "ok" if tool_ok else "fail",
        "available" if tool_ok else "not found",
        "" if tool_ok else "The GPU Driver tab requires ubuntu-drivers (Ubuntu only).")

    net = result.get("net") or {}
    pypi_sources = ("https://pypi.org/simple/",
                    "https://pypi.tuna.tsinghua.edu.cn/simple/",
                    "https://mirrors.aliyun.com/pypi/simple/")
    reachable = [u for u in pypi_sources if (net.get(u) or "").isdigit() and int(net[u]) < 500]
    net_ok = bool(reachable)
    add("network", "Network", "ok" if net_ok else "fail",
        "reachable: " + ", ".join(u.split("//")[1].split("/")[0] for u in reachable) if net_ok
        else "no PyPI source reachable",
        "" if net_ok else "Configure a reachable mirror in the Mirror Sources panel.")
    hf_code = net.get("https://hf-mirror.com/", "")
    add("hf_mirror", "HF mirror", "ok" if hf_code.isdigit() and int(hf_code) < 500 else "warn",
        "reachable" if hf_code.isdigit() and int(hf_code) < 500 else "unreachable",
        "" if hf_code.isdigit() and int(hf_code) < 500 else "Model downloads may be slow/blocked.")

    blocking = any(i["status"] == "fail" for i in items if i["key"] in ("disk", "python", "uv"))
    return {"ok": True, "blocking": blocking, "items": items, "checked_at": ds._now_iso()}


# ===========================================================================
# Tab 1 — GPU driver
# ===========================================================================

@router.get("/drivers")
async def get_drivers():
    _require_remote()
    ds.reconcile_driver_task()
    executor = _dep.get_remote_executor()
    info = await _probe_in_thread(executor.get_driver_list, 45)
    if not info.get("success"):
        raise HTTPException(status_code=500, detail=info.get("error") or "Driver query failed")
    current = info.get("current_driver", "")
    major = ds._major_driver_version(current)
    # dpkg gives the exact apt package(s) installed — mark only those rows.
    # No version-number fallback: an out-of-band (.run) driver leaves no apt
    # record, and flagging every variant that shares the major version
    # (server / open / …) is exactly the over-marking to avoid.
    installed_packages = set(info.get("installed_packages") or [])
    rows = []
    for row in info["drivers"]:
        row = dict(row)
        row["installed"] = row["package"] in installed_packages
        rows.append(row)
    rows.sort(key=_driver_sort_key)
    pending = ds.task_manager.pending_driver_task()
    # Latest driver-kind task (any status) with its log tail — the driver
    # tab renders live progress and outcomes in place of a manual trip to
    # the task center.
    active = next((t for t in ds.task_manager.list(50) if t.kind == "driver"), None)
    active_data = None
    if active is not None:
        active_data = active.to_dict()
        # In-memory tail first; after a dashboard restart the deque is empty,
        # so fall back to the log file so finished tasks still show their
        # last lines in the driver tab panel.
        active_data["log_tail"] = (
            ds.task_manager.tail(active.id, 12) or ds.task_manager.file_tail(active.id, 12)
        )
    return {
        "gpu_models": info.get("gpu_models", []),
        "drivers": rows,
        "current_driver": current,
        "current_major": major,
        "min_driver": VLLM_MIN_DRIVER,
        "driver_min_cuda": {str(k): v for k, v in DRIVER_MIN_CUDA.items()},
        "pending_task": (
            {"id": pending.id, "package": pending.result.get("package", ""), "status": pending.status}
            if pending else None
        ),
        "active_task": active_data,
    }


@router.post("/drivers/apply")
async def apply_driver(body: dict[str, Any]):
    _require_remote()
    package = str(body.get("package") or "")
    try:
        task = await asyncio.to_thread(ds.start_driver_task, package)
    except DeploymentError as e:
        raise _error_to_http(e) from e
    return {"status": "started", "task_id": task.id}


@router.post("/reboot")
async def reboot_server():
    _require_remote()
    try:
        task = await asyncio.to_thread(ds.start_reboot_task)
    except DeploymentError as e:
        raise _error_to_http(e) from e
    return {"status": "started", "task_id": task.id}


# ===========================================================================
# Tab 2 — CUDA toolkit
# ===========================================================================

@router.get("/cuda")
async def get_cuda():
    _require_remote()
    executor = _dep.get_remote_executor()
    info = await _probe_in_thread(executor.get_driver_list, 45)
    driver_major = ds._major_driver_version(info.get("current_driver", ""))
    state = ds.deployment_state.get()
    result = await _probe_in_thread(
        lambda: executor.execute(CUDA_VERSION_PROBE, 20),
        30,
    )
    toolkit = (result.get("stdout") or "").strip()
    if toolkit == "NO_NVCC":
        toolkit = ""
    selected_pkg = state["selected"].get("driver", "")
    selected_major = ds._driver_pkg_major(selected_pkg)
    return {
        "versions": list(_CUDA_VERSIONS),
        "min_driver": ds.CUDA_MIN_DRIVER,
        "selected": state["selected"].get("cuda", ""),
        "install_system": bool(state["selected"].get("cuda_install_system", False)),
        "driver_major": driver_major,
        "selected_driver_major": selected_major,
        "driver_max_cuda": ds.driver_max_cuda(driver_major),
        "selected_driver_max_cuda": ds.driver_max_cuda(selected_major),
        "current_toolkit": toolkit,
        "installed_toolkit": state["snapshots"]["cuda"].get("installed_version", ""),
    }


@router.post("/cuda/apply")
async def apply_cuda(body: dict[str, Any]):
    _require_remote()
    version = str(body.get("version") or "")
    install_system = bool(body.get("install_system", True))
    try:
        task = await asyncio.to_thread(ds.start_cuda_task, version, install_system)
    except DeploymentError as e:
        raise _error_to_http(e) from e
    if task is None:
        return {"status": "saved", "task_id": None,
                "message": "Selection saved — no system CUDA install requested"}
    return {"status": "started", "task_id": task.id}


# ===========================================================================
# Tab 3 — vLLM
# ===========================================================================

@router.get("/vllm")
async def get_vllm():
    _require_remote()
    executor = _dep.get_remote_executor()
    env_info = await _probe_in_thread(executor.get_vllm_env, 45)
    if not env_info.get("success"):
        raise HTTPException(status_code=500, detail=env_info.get("error") or "vLLM env query failed")
    state = ds.deployment_state.get()
    return {
        **env_info,
        "venv_name": _dep.config_manager.get_config().venv_name,
        "cuda_versions": list(_CUDA_VERSIONS),
        "selected": {
            "version": state["selected"].get("vllm_version", ""),
            "runtime": state["selected"].get("vllm_runtime", "builtin"),
            "cuda": state["selected"].get("cuda", ""),
        },
        "snapshot": state["snapshots"]["vllm"],
    }


@router.post("/vllm/apply")
async def apply_vllm(body: dict[str, Any]):
    _require_remote()
    allowed = {
        "version", "runtime_mode", "env_mode", "venv_name", "python_version",
        "source_build", "auto_register_service", "cuda_version",
    }
    payload = {k: body[k] for k in allowed if k in body}
    try:
        task = await asyncio.to_thread(ds.start_vllm_task, payload)
    except DeploymentError as e:
        raise _error_to_http(e) from e
    return {"status": "started", "task_id": task.id}
