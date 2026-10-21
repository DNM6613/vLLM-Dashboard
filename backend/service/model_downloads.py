"""Model downloads and CLI tool install status.

Split out of ``model_service.py`` (file-size gate). Shared download state
(``_dl_total_cache`` / ``_dl_growth``) and the executor hook live on the
``model_service`` facade and are resolved through it.
"""

from __future__ import annotations

import logging
import shlex

from fastapi import HTTPException

from ..executor.remote.model_ops import DL_PROBE_SENTINEL, DL_PROCESS_PATTERN
from . import model_service as _ms
from .model_download_status import (
    _build_download_status_command as _build_download_status_command,
)
from .model_download_status import (
    _get_download_total as _get_download_total,
)
from .model_download_status import (
    get_download_status as get_download_status,
)
from .model_download_status import (
    stop_download as stop_download,
)

logger = logging.getLogger(__name__)


def download_model(model_repo: str, model_save_path: str, hf_mirror: bool) -> dict:
    from ..config.remote_client import config_manager

    executor = _ms.get_remote_executor()

    # Duplicate-download guard (audit 2026-10-20 P3-3): the frontend only
    # closes the same-frame double-click window; a second tab or a manual
    # API call could still start a second `hf download` for the same repo
    # (two writers on one cache dir corrupt partial files). Probe for a
    # live download of this repo and refuse with 409. The sentinel trick
    # mirrors stop_download: the probe's own pipeline carries the sentinel
    # in its cmdline and is excluded by the first grep -v, while a real
    # download (no sentinel in its cmdline) survives.
    repo_q = shlex.quote(model_repo)
    probe = (
        "P=$(ps -eo pid=,args= 2>/dev/null "
        "| grep -v '" + DL_PROBE_SENTINEL + "' "
        "| grep -E '" + DL_PROBE_SENTINEL + "|" + DL_PROCESS_PATTERN + "' "
        "| grep -F " + repo_q + " "
        "| awk '{print $1}' | head -1); "
        'echo "EXIST=${P:-NONE}"'
    )
    probe_res = executor.execute(probe)
    probe_out = (probe_res.get("stdout") or "").strip()
    if probe_res.get("success") and probe_out.startswith("EXIST="):
        existing = probe_out[len("EXIST="):].strip()
        if existing and existing != "NONE":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A download of {model_repo} is already in progress "
                    f"(pid {existing}) — wait for it to finish or stop it first"
                ),
            )

    try:
        mirror = executor.set_hf_mirror(hf_mirror)
        if not mirror.get("success", False):
            logger.warning(f"Failed to configure HF mirror (enabled={hf_mirror}): {mirror.get('error', mirror.get('message', ''))}")
    except Exception as e:
        logger.warning(f"Failed to configure HF mirror (enabled={hf_mirror}): {e}")

    result = executor.download_model(
        model_repo=model_repo,
        model_save_path=model_save_path if model_save_path else "",
        hf_mirror=hf_mirror,
    )

    if not result["success"]:
        logger.error(f"Download failed for {model_repo}: {result.get('stderr', '')}")
        raise HTTPException(status_code=500, detail="Download failed, see server logs for details")

    if model_save_path:
        try:
            if (config_manager.get_config().model_save_path or "") != model_save_path:
                config_manager.merge_config({"model_save_path": model_save_path})
        except Exception as e:
            logger.warning(f"Failed to persist model_save_path={model_save_path!r}: {e}")

    try:
        saved_path = config_manager.get_config().model_save_path or ""
    except Exception:
        saved_path = ""
    return {
        "status": "success",
        "pid": int(result["stdout"].strip()) if result["stdout"].strip().isdigit() else None,
        "log_file": result.get("log_file", ""),
        "model_save_path": saved_path,
    }


def check_cli_tools() -> dict:
    executor = _ms.get_remote_executor()
    return executor.check_cli_tools()


def install_cli_tool(tool: str) -> dict:
    executor = _ms.get_remote_executor()
    result = executor.install_cli_tool(tool)

    if not result["success"]:
        logger.error(f"Install {tool} failed: {result.get('stderr', '')}")
        raise HTTPException(status_code=500, detail="Install failed to start, see server logs for details")

    return {
        "status": "success",
        "pid": int(result["stdout"].strip()) if result["stdout"].strip().isdigit() else None,
        "log_file": result.get("log_file", ""),
    }


def get_install_status(tool: str, log_file: str) -> dict:
    executor = _ms.get_remote_executor()
    return executor.get_install_status(tool, log_file)


def ensure_hf_mirror() -> dict:
    executor = _ms.get_remote_executor()
    result = executor.ensure_hf_mirror()

    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to configure HF mirror"))

    return result
