"""Deployment public start_* entry points (called by the API layer).

Split out of ``deployment_service.py`` (file-size gate). Pipelines live in
``deployment_pipelines_env.py`` / ``deployment_pipelines_vllm.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config.remote_client import config_manager
from ..executor.remote.deployment_ops import CUDA_VERSION_PROBE
from ..executor.remote_executor import get_remote_executor
from . import deployment_service as _ds
from .deployment_common import (
    CUDA_VER_RE,
    DRIVER_PKG_RE,
    PY_VER_RE,
    VLLM_VER_RE,
    DeploymentError,
    _major_driver_version,
    _resolve_venv_path,
    _validate_venv_name,
)
from .deployment_pipelines_env import _run_cuda, _run_driver, check_cuda_compat
from .deployment_pipelines_vllm import (
    _check_runtime_toolkit_compat,
    _run_reboot,
    _run_rollback_vllm,
    _run_vllm,
)
from .deployment_task_ctx import Task

logger = logging.getLogger(__name__)


def _require_remote() -> None:
    config = config_manager.get_config()
    if not config.host or config.host in ("localhost", "127.0.0.1"):
        raise DeploymentError(
            "Environment deployment requires a remote SSH server (set the host in Server Config)"
        )


def start_driver_task(package: str) -> Task:
    _require_remote()
    if not DRIVER_PKG_RE.fullmatch(package or ""):
        raise DeploymentError(f"Invalid driver package name: {package}")
    executor = get_remote_executor()
    info = executor.get_driver_list()
    if not info.get("success"):
        raise DeploymentError(info.get("error") or "Failed to query driver list")
    target_major: int | None = None
    for row in info["drivers"]:
        if row["package"] == package:
            target_major = row["version"]
            break
    if target_major is None:
        raise DeploymentError(f"Driver {package} is not in the available list")

    installed = executor.list_installed_driver_packages()
    current_pkg = installed["packages"][0]["name"] if installed.get("packages") else ""
    current_major = _major_driver_version(info.get("current_driver", ""))
    recorded = _ds.deployment_state.get()["snapshots"]["driver"]["current_pkg"]
    _ds.deployment_state.set_snapshot(
        "driver",
        previous_pkg=current_pkg or recorded,
        previous_version=str(current_major) if current_major else "",
    )
    _ds.deployment_state.update_section("selected", driver=package)

    task = _ds.task_manager.create("driver", f"Apply driver {package}",
                                   ["Stop vLLM service", "Uninstall old driver",
                                    "Install target driver", "Wait for reboot"])
    task.result = {"package": package, "target_major": target_major,
                   "current_driver": info.get("current_driver", "")}
    _ds.task_manager.submit(task, lambda ctx: _run_driver(ctx, package, target_major))
    return task


def start_cuda_task(version: str, install_system: bool) -> Task | None:
    _require_remote()
    if not install_system:
        # Skip mode: no CUDA version is selected or needed — clear any
        # previous selection and persist the skip flag.
        _ds.deployment_state.update_section("selected", cuda="",
                                            cuda_install_system=False)
        return None
    if not CUDA_VER_RE.fullmatch(version or ""):
        raise DeploymentError(f"Invalid CUDA version: {version}")
    executor = get_remote_executor()
    info = executor.get_driver_list()
    if not info.get("success"):
        raise DeploymentError(info.get("error") or "Failed to query driver version")
    driver_major = _major_driver_version(info.get("current_driver", ""))
    problem = check_cuda_compat(driver_major, version)
    if problem:
        raise DeploymentError(problem)

    _ds.deployment_state.update_section("selected", cuda=version,
                                        cuda_install_system=install_system)

    pkg = f"cuda-toolkit-{version.replace('.', '-')}"
    task = _ds.task_manager.create("cuda", f"Install CUDA toolkit {version}",
                                   ["Add NVIDIA CUDA repo", f"Install {pkg}",
                                    "Write CUDA environment", "Verify nvcc"])
    task.result = {"version": version, "package": pkg}
    _ds.task_manager.submit(task, lambda ctx: _run_cuda(ctx, version, pkg))
    return task


def start_vllm_task(payload: dict[str, Any]) -> Task:
    _require_remote()
    version = (payload.get("version") or "latest").strip()
    if version != "latest" and not VLLM_VER_RE.fullmatch(version):
        raise DeploymentError(f"Invalid vLLM version: {version}")
    if payload.get("runtime_mode") not in ("builtin", "system"):
        raise DeploymentError("Invalid CUDA runtime mode")
    if payload.get("env_mode") not in ("new", "existing"):
        raise DeploymentError("Invalid Python environment mode")
    venv_name = (payload.get("venv_name") or "").strip() or config_manager.get_config().venv_name or ".vllm"
    _validate_venv_name(venv_name)
    py_version = (payload.get("python_version") or "").strip()
    if py_version and not PY_VER_RE.fullmatch(py_version):
        raise DeploymentError(f"Invalid Python version: {py_version}")

    state = _ds.deployment_state.get()
    # The client sends the effective selection (skip-system-CUDA mode sends
    # no version); an empty version means a plain vllm install.
    cuda_version = (payload.get("cuda_version") or "").strip()
    payload = {**payload, "venv_name": venv_name, "python_version": py_version,
               "cuda_version": cuda_version}

    executor = get_remote_executor()
    if state["selected"].get("cuda_install_system"):
        effective_cuda = cuda_version
    else:
        # system install skipped: the effective toolkit is whatever the server
        # already runs (manual installs included)
        probe = executor.execute(CUDA_VERSION_PROBE, 20)
        effective_cuda = (probe.get("stdout") or "").strip()
        if effective_cuda == "NO_NVCC":
            effective_cuda = ""
    payload["effective_cuda"] = effective_cuda
    _check_runtime_toolkit_compat(payload["runtime_mode"], cuda_version, effective_cuda)
    env_info = executor.get_vllm_env()
    previous_version = env_info.get("vllm_version", "") if env_info.get("success") else ""

    _ds.deployment_state.update_section("selected", vllm_version=version,
                                        vllm_runtime=payload["runtime_mode"])
    venv_path = _resolve_venv_path(venv_name)
    title = (f"Install vLLM {version}" if version != "latest" else "Install vLLM (latest)")
    steps = ["Stop vLLM service", "Prepare Python environment", "Install vLLM",
             "Verify version"]
    if payload.get("auto_register_service"):
        steps.append("Write supervisor template")
    task = _ds.task_manager.create("vllm", title, steps)
    task.result = {"version": version, "venv": venv_name, "previous_version": previous_version}
    _ds.task_manager.submit(task, lambda ctx: _run_vllm(ctx, payload, venv_path, previous_version))
    return task


def start_rollback_driver() -> Task:
    _require_remote()
    state = _ds.deployment_state.get()
    previous = (state["snapshots"]["driver"].get("previous_pkg") or "").strip()
    if not previous:
        raise DeploymentError("No previous driver recorded — nothing to roll back to")
    if not DRIVER_PKG_RE.fullmatch(previous):
        raise DeploymentError(f"Invalid recorded driver package: {previous}")
    return start_driver_task(previous)


def start_rollback_vllm() -> Task:
    _require_remote()
    state = _ds.deployment_state.get()
    target = (state["snapshots"]["vllm"].get("previous_version") or "").strip()
    if not target:
        raise DeploymentError("No previous vLLM version recorded — nothing to roll back to")
    if not VLLM_VER_RE.fullmatch(target):
        raise DeploymentError(f"Invalid recorded vLLM version: {target}")
    venv_name = (state["snapshots"]["vllm"].get("venv")
                 or config_manager.get_config().venv_name or ".vllm")
    _validate_venv_name(venv_name)
    venv_path = _resolve_venv_path(venv_name)
    task = _ds.task_manager.create("rollback_vllm", f"Roll back vLLM to {target}",
                                   ["Stop vLLM service", f"Reinstall vllm=={target}", "Verify version"])
    task.result = {"target_version": target, "venv": venv_name}
    _ds.task_manager.submit(
        task,
        lambda ctx: _run_rollback_vllm(ctx, target, venv_path, _ds.deployment_state.get()["mirrors"]),
    )
    return task


def start_reboot_task() -> Task:
    _require_remote()
    task = _ds.task_manager.create("reboot", "Reboot server", ["Reboot server"])
    _ds.task_manager.submit(task, _run_reboot)
    return task


def reconcile_driver_task() -> Task | None:
    """Verify a post-reboot driver install (called from read endpoints)."""
    task = _ds.task_manager.pending_driver_task()
    if task is None:
        return None
    try:
        executor = get_remote_executor()
        result = executor.execute(
            "nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1",
            timeout=15,
        )
    except Exception as e:
        logger.info("driver reconcile skipped (server not reachable): %s", e)
        return task
    if not result.get("success"):
        return task
    current_major = _major_driver_version((result.get("stdout") or "").strip())
    target_major = task.result.get("target_major")
    if current_major is not None and target_major is not None and current_major == target_major:
        _ds.task_manager.complete_driver(task)
        _ds.deployment_state.set_snapshot("driver",
                                          current_pkg=task.result.get("package", ""),
                                          previous_version=str(current_major))
        _ds.task_manager.log(task.id, f"Reboot verified: driver {current_major} active. Task complete.")
    return task
