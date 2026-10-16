"""Deployment service — shared vocabulary: errors, compat tables, validation.

Split out of ``deployment_service.py`` (file-size gate). Cross-module shared
state (path constants, singletons) stays in the ``deployment_service``
facade; submodules resolve it via that facade so existing imports and test
patches keep working.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..config.server_config import has_parent_path_segment

# ---- compatibility rules (spec) -------------------------------------------
# Minimum driver major per CUDA toolkit version — NVIDIA CUDA Toolkit release
# notes, "Corresponding Driver Versions" table (Linux x86_64, GA rows):
# 12.9 >= 575.51.03, 13.0 >= 580.65.06, 13.1 >= 590.44.01, 13.2 >= 595.45.04,
# 13.3 >= 610.43.02; CUDA 13.4 corresponds to the R615 driver branch (13.4.1
# notes). A toolkit runs on any driver at/above its minimum, so a driver's
# practical CUDA ceiling is the highest version it meets — driver 595 tops
# out at 13.2 (verified 2026-09-18: nvidia-smi reports "CUDA Version: 13.2"
# for 595.91.07).
CUDA_MIN_DRIVER: dict[str, int] = {
    "12.9": 575, "13.0": 580, "13.1": 590, "13.2": 595,
    "13.3": 610, "13.4": 615,
}
# Minimum driver for vLLM's default install: the PyPI wheel is compiled with
# CUDA 12.9, whose minimum driver is 575.51.03 (NVIDIA CUDA release notes);
# vLLM's docs state no independent driver floor of their own.
VLLM_MIN_DRIVER = 575
# Driver -> minimum CUDA floor. NVIDIA documents drivers as backward
# compatible with older toolkits (no maximum driver per toolkit), so this
# stays empty; the binding direction is CUDA -> driver (CUDA_MIN_DRIVER).
DRIVER_MIN_CUDA: dict[int, str] = {}

# ---- input validation ------------------------------------------------------
DRIVER_PKG_RE = re.compile(r"^nvidia-driver-\d{3,4}(-[a-z0-9]+)*$")
CUDA_VER_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")
VLLM_VER_RE = re.compile(r"^\d+\.\d+\.\d+$")
VENV_NAME_RE = re.compile(r"^[\w./\-~]{1,128}$")
PY_VER_RE = re.compile(r"^\d+\.\d{1,2}$")
URL_RE = re.compile(r"^https?://[\w.\-]+(?::\d+)?(/\S*)?$")

TASK_STATUSES = ("queued", "running", "success", "failed",
                 "cancelled", "awaiting_reboot", "interrupted")
TERMINAL_STATUSES = ("success", "failed", "cancelled", "interrupted")


class DeploymentError(Exception):
    """Fatal pipeline failure with a user-facing message."""


class TaskCancelled(Exception):
    """Raised between steps when the user cancels a task."""


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _major_driver_version(version: str) -> int | None:
    m = re.match(r"^(\d{3,4})\.", (version or "").strip())
    return int(m.group(1)) if m else None


def _validate_venv_name(venv_name: str) -> None:
    if not VENV_NAME_RE.fullmatch(venv_name or "") or has_parent_path_segment(venv_name):
        raise DeploymentError(f"Invalid venv name: {venv_name}")


def _resolve_venv_path(venv_name: str) -> str:
    """Remote shell path for the venv (same semantics as RemoteExecutor
    ``_get_activate_cmd``): bare name under $HOME, ``~/...`` and ``/abs``."""
    if venv_name.startswith("/"):
        return venv_name
    if venv_name == "~":
        return "$HOME"
    if venv_name.startswith("~/"):
        return f"$HOME/{venv_name[2:]}"
    return f"$HOME/{venv_name}"


def _suggest_error(message: str) -> str:
    m = message.lower()
    if "timed out" in m or "timeout" in m:
        return "Network or package repository is slow. Try switching the mirror source (Mirror Sources panel) and retry."
    if "unable to locate package" in m or "no candidate" in m:
        return "Package not found in the configured repositories. Check the selected version."
    if "could not get lock" in m or "dpkg lock" in m:
        return "Another apt/dpkg process is running. Wait for it to finish, then retry."
    if "no space left" in m:
        return "Insufficient disk space. Free up space and retry."
    if "password" in m or "authentication" in m or "sudo" in m:
        return "sudo authentication failed. The SSH password must match the sudo password."
    if "could not resolve" in m or "unreachable" in m or "curl" in m:
        return "Network unreachable from the target server. Check its egress / mirror configuration."
    return ""
