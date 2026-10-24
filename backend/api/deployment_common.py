"""Deployment API — shared endpoint helpers.

Split out of ``api/deployment.py`` (file-size gate). Route groups live in
``api/deployment_routes_env.py`` / ``api/deployment_routes_tasks.py``.
``config_manager`` and ``get_remote_executor`` are read through the
``api.deployment`` facade so tests patching those names keep working.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from ..service import deployment_version_probe as version_probe
from ..service.deployment_common import (
    _CUDA_VERSIONS as _CUDA_VERSIONS,
)
from ..service.deployment_service import DeploymentError
from . import deployment as _dep


async def _cuda_versions_dynamic(executor) -> tuple[list[str], str]:
    """Live NVIDIA repo toolkit list (TTL-cached) or the static fallback.

    /cuda is polled every 5 s while the modal is open, so the repo fetch
    must not ride the poll — the probe module's cache + in-flight claim
    bound it. Any failure (stalled SSH, CDN down, unparseable index)
    degrades to the static list with source "fallback" so the tab always
    renders; the wall-clock cap keeps a wedged probe from holding the
    endpoint (same posture as _probe_in_thread).
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(version_probe.probe_cuda_repo_versions, executor), 75)
    except Exception:
        return list(_CUDA_VERSIONS), "fallback"


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
    config = _dep.config_manager.get_config()
    if not config.host or config.host in ("localhost", "127.0.0.1"):
        raise HTTPException(
            status_code=400,
            detail="Environment deployment requires a remote SSH server (set the host in Server Config)",
        )


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
