"""Model lifecycle: start / stop (remote stop verification, env unset).

Split out of ``model_service.py`` (file-size gate). Shared state and patch
hooks live on the ``model_service`` facade and are resolved through it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import time

from fastapi import HTTPException

from ..config.model_launch_config import launch_config_manager, parse_env_vars
from ..config.remote_client import config_manager
from ..console.session_manager import console_session_manager
from ..executor.remote.process_ops import _STOP_SELF_SENTINEL, _VLLM_MAIN_PATTERN, _ps_filter_self
from ..schemas.model import ModelStatus
from . import model_service as _ms

logger = logging.getLogger(__name__)


def _build_stop_wait_command(model_path: str) -> str:
    if not model_path:
        return (
            f"{_STOP_SELF_SENTINEL}=1; "
            "gpu_pids=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); "
            "vllm_gpu=''; "
            "for pid in $gpu_pids; do "
            "  if ps -p $pid -o cmd= 2>/dev/null | grep -qi 'vllm'; then "
            "    vllm_gpu=\"$vllm_gpu $pid\"; "
            "  fi; "
            "done; "
            f"main_pids=$({_ps_filter_self(_VLLM_MAIN_PATTERN)}); "
            "if [ -z \"$vllm_gpu\" ] && [ -z \"$main_pids\" ]; then echo CLEAN; else echo BUSY; fi"
        )
    mp = shlex.quote(model_path)
    return (
        f"{_STOP_SELF_SENTINEL}=1; "
        f"mp={mp}; "
        f"main_pids=$(ps -eo pid=,args= 2>/dev/null | grep -v '{_STOP_SELF_SENTINEL}' | grep -F \"$mp\" | grep -i 'vllm' | awk '{{print $1}}'); "
        "gpu_hit=''; "
        "for gpid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do "
        "  if ps -p $gpid -o args= 2>/dev/null | grep -Fq \"$mp\"; then gpu_hit=$gpid; fi; "
        "done; "
        "if [ -z \"$main_pids\" ] && [ -z \"$gpu_hit\" ]; then echo CLEAN; else echo BUSY; fi"
    )


async def _wait_for_remote_stop(executor, max_wait: int = 8, model_path: str = "") -> bool:
    # Wall-clock deadline: SSH round-trips are not instantaneous, so counting
    # logical iterations let the real wait exceed max_wait. Check first (the
    # stop command already ran), then poll every second until the deadline.
    deadline = time.monotonic() + max_wait
    cmd = _build_stop_wait_command(model_path)
    while True:
        check = await asyncio.to_thread(executor.execute, cmd)
        if check["success"] and "CLEAN" in check["stdout"]:
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(1)


async def _unset_launch_env_vars(model_id: str) -> None:
    """Unset the environment variables declared in the model's launch config in the
    persistent console PTY — the same shell where the start command's `export`s live."""
    config = launch_config_manager.load_config(model_id)
    if not config:
        return
    try:
        parsed_env = parse_env_vars(config.get("env_vars", ""))
    except ValueError as e:
        logger.warning(f"Failed to parse launch env vars for unset (model {model_id}): {e}")
        return
    keys = [k for k, _v in parsed_env]
    if not keys:
        return
    session = await asyncio.to_thread(
        console_session_manager.get_session, config_manager.get_config()
    )
    if session is None:
        return
    cmd = "unset " + " ".join(keys) + "\n"
    if session.send_command(cmd):
        logger.info(f"Unset {len(keys)} launch env var(s) in console session for model {model_id}")
    else:
        logger.warning(f"Failed to send unset command to console session for model {model_id}")


async def _verify_start_probe(model_id: str, api_host: str, api_port: int) -> None:
    # The console PTY auto-sends the start command a few seconds after
    # start_model returns. Verify the vLLM process actually appeared, so a
    # dead console does not leave the model stuck in LOADING until the
    # 2-minute sync grace expires. Roll back only on a definite negative —
    # an unknown probe result keeps the status for the sync loop to settle.
    await asyncio.sleep(_ms._START_PROBE_DELAY)
    model = _ms.state_machine.get_model(model_id)
    if not model or model.status != ModelStatus.LOADING:
        return
    present = await _ms._check_vllm_present_strict(api_host, api_port)
    if present is False:
        logger.warning(
            f"No vLLM process detected {_ms._START_PROBE_DELAY:.0f}s after start "
            f"for {model_id} — rolling back to STOPPED")
        _ms.state_machine.update_model_status(model_id, ModelStatus.STOPPED)
        _ms.state_machine.clear_current_model(model_id)
        # A silent start failure (e.g. the console PTY was not ready and the
        # start command was never delivered) must not orphan the watchdog:
        # re-arm the crash event so the next attempt retries, mirroring the
        # sync path's LOADING→STOPPED handling (incident 2026-10-17).
        if model.locked:
            _ms._watchdog_on_crash(model_id)


async def _start_model(model_id: str) -> dict:
    model = _ms.state_machine.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model.status in (ModelStatus.RUNNING, ModelStatus.LOADING):
        raise HTTPException(status_code=409, detail=f"Model is already {model.status.value}")

    ssh = await asyncio.to_thread(_ms.check_ssh_connectivity, True)
    if not ssh.get("connected"):
        raise HTTPException(
            status_code=503,
            detail=f"AI server unreachable via SSH — model start requires SSH ({ssh.get('error', 'unknown error')})"
        )

    config = launch_config_manager.load_config(model_id)
    if not config or not config.get("start_command"):
        raise HTTPException(status_code=400, detail="Start command not configured")

    try:
        parsed_env = parse_env_vars(config.get("env_vars", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid environment variables: {e}")

    _ms.state_machine.update_model_status(model_id, ModelStatus.LOADING)
    _ms.state_machine.set_current_model(model_id)
    _ms._mark_auto_bench_pending(model_id)

    executor = _ms.get_remote_executor()
    activate_cmd = executor._get_activate_cmd()
    start_command = config["start_command"]
    if activate_cmd:
        start_command = re.sub(r'^source\s+\S*activate\S*\s*(?:&&\s*|\n)?', '', start_command).lstrip()
        full_command = f"{activate_cmd}{start_command}"
    else:
        full_command = start_command

    if parsed_env:
        full_command = "".join(
            f"export {key}={shlex.quote(value)} && " for key, value in parsed_env
        ) + full_command

    server_config = config_manager.get_config()
    target = _ms.resolve_api_target(server_config)
    task = asyncio.create_task(
        _ms._verify_start_probe(model_id, server_config.host, target.port))
    _ms._start_probe_tasks.add(task)
    task.add_done_callback(_ms._start_probe_tasks.discard)

    return {
        "status": "loading",
        "model_id": model_id,
        "command": full_command
    }


async def start_model(model_id: str) -> dict:
    async with _ms._start_lock:
        return await _start_model(model_id)


async def stop_model(model_id: str) -> dict:
    executor = _ms.get_remote_executor()

    model = _ms.state_machine.get_model(model_id)
    model_path = model.path if model else ""

    stop_result = await asyncio.to_thread(executor.stop_vllm, force=True, model_path=model_path)
    if stop_result.get("mode") == "global":
        logger.warning(
            f"stop_vllm fell back to GLOBAL kill for model {model_id} "
            f"(model_path={model_path!r} not matched — external launch or stale path)"
        )

    wait_path = model_path if stop_result.get("mode") == "scoped" else ""
    stopped = await _wait_for_remote_stop(executor, max_wait=5, model_path=wait_path)
    if not stopped:
        logger.warning(f"vLLM process still running after stop for model {model_id}")

    model = _ms.state_machine.get_model(model_id)
    if model and stopped:
        _ms.state_machine.update_model_status(model_id, ModelStatus.STOPPED)
        _ms.state_machine.clear_current_model(model_id)

    await _unset_launch_env_vars(model_id)

    result = {"status": "success" if stopped else "timeout", "stopped": stopped}
    if not stopped and stop_result.get("error"):
        result["error"] = stop_result["error"]
    return result
