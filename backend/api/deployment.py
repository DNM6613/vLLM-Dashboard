"""Environment-deployment API: driver / CUDA / vLLM, tasks, state, rollback.

All mutation endpoints create async tasks (see ``service/deployment_service``);
read endpoints run short SSH probes. ``DeploymentError`` from the service
layer maps to HTTP 400.
"""

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..config.remote_client import config_manager
from ..executor.remote.deployment_ops import CUDA_VERSION_PROBE
from ..executor.remote_executor import get_remote_executor
from ..service import deployment_service as ds
from ..service.deployment_service import (
    DRIVER_MIN_CUDA,
    VLLM_MIN_DRIVER,
    DeploymentError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deployment", tags=["deployment"])

# Offered toolkits, 12.9–13.4. On the Ubuntu 26.04 target the ubuntu2604
# repo only carries 13.3/13.4 (verified 2026-09-18 by listing
# ubuntu2604/x86_64 Packages); the ubuntu2404 repo carries 12.9–13.4 and its
# toolkits install cleanly on 26.04 (user-verified 2026-09-18: 12.9/13.0).
# The per-version keyring switch lives in deployment_service._run_cuda.
_CUDA_VERSIONS = ["12.9", "13.0", "13.1", "13.2", "13.3", "13.4"]


def _error_to_http(e: DeploymentError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


async def _probe_in_thread(fn, timeout: int = 45):
    """Run a blocking SSH probe with a hard wall-clock bound.

    The executor's per-command timeout only covers opening the SSH channel —
    reading the output is unbounded, so a stalled remote (e.g. the AI server's
    sshd intermittently not sending its protocol banner) can hold a read
    endpoint, and its worker thread, indefinitely.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
    except TimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail="Remote probe timed out — the AI server SSH is intermittently slow; retry shortly") from e


def _require_remote() -> None:
    config = config_manager.get_config()
    if not config.host or config.host in ("localhost", "127.0.0.1"):
        raise HTTPException(
            status_code=400,
            detail="Environment deployment requires a remote SSH server (set the host in Server Config)",
        )


# ===========================================================================
# Preflight
# ===========================================================================

def _disk_free_gb(disk: list[str]) -> float | None:
    if not disk:
        return None
    try:
        return int(disk[0]) / (1024 * 1024)
    except (ValueError, IndexError):
        return None


def _driver_sort_key(row: dict[str, Any]) -> tuple[bool, int, str]:
    """Newest major version first, versionless rows last, package name breaks
    ties. `ubuntu-drivers devices` output order is NOT stable run-to-run, so
    without an explicit sort the 5s polling visibly shuffles the list."""
    version = row.get("version")
    return (version is None, -(version or 0), row.get("package", ""))


@router.get("/preflight")
async def get_preflight():
    _require_remote()
    executor = get_remote_executor()
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
    executor = get_remote_executor()
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
        if row.get("nouveau"):
            row["note"] = "nouveau is a fallback driver with poor performance — not suitable for vLLM"
        rows.append(row)
    rows.sort(key=_driver_sort_key)
    pending = ds.task_manager.pending_driver_task()
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
    executor = get_remote_executor()
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
    executor = get_remote_executor()
    env_info = await _probe_in_thread(executor.get_vllm_env, 45)
    if not env_info.get("success"):
        raise HTTPException(status_code=500, detail=env_info.get("error") or "vLLM env query failed")
    state = ds.deployment_state.get()
    return {
        **env_info,
        "venv_name": config_manager.get_config().venv_name,
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
        "source_build", "flashinfer", "nccl",
        "auto_register_service", "cuda_version",
    }
    payload = {k: body[k] for k in allowed if k in body}
    try:
        task = await asyncio.to_thread(ds.start_vllm_task, payload)
    except DeploymentError as e:
        raise _error_to_http(e) from e
    return {"status": "started", "task_id": task.id}


# ===========================================================================
# Task center
# ===========================================================================

@router.get("/tasks")
async def list_tasks(limit: int = Query(default=30, ge=1, le=100)):
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
# State / mirrors / rollback / conflicts / templates / export
# ===========================================================================

@router.get("/state")
async def get_state():
    state = ds.deployment_state.get()
    executor = get_remote_executor()
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
        values = {}
        for key, conv in allowed_selected.items():
            if key in selected:
                values[key] = conv(selected[key])
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
