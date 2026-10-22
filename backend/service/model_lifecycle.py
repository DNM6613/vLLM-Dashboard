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

# Audit 2026-10-22 BCK-5: models currently being stopped by a user action.
# _verify_start_probe must not interpret the expected "process gone" as a
# start crash while a manual stop is in flight — that path would roll the
# status back to STOPPED, record a crash, and let the watchdog re-launch
# the model the user just stopped. The same flag guards the status-sync
# crash hooks (model_status_sync): during the stop window the process is
# already dead while the status is still RUNNING/LOADING, and without the
# guard the sync records a "crash" for a deliberate stop (incident
# 2026-10-22 stop↔auto-restart loop on a locked model).
_stopping_models: set[str] = set()


def is_stopping(model_id: str) -> bool:
    """True while a user-initiated stop is in flight for the model."""
    return model_id in _stopping_models


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
        # Audit 2026-10-22 REM-4: a leaked VLLM::Worker grandchild does not
        # carry the model path in its cmdline, so the checks above would call
        # the stop CLEAN while the worker still holds the GPU. The platform
        # runs a single current model, so any live VLLM::Worker means this
        # stop is not finished. The probe chain's own cmdlines carry the
        # sentinel and are filtered by grep -v (same trick as main_pids).
        f"worker_cnt=$(ps -eo cmd= 2>/dev/null | grep -v '{_STOP_SELF_SENTINEL}' | grep -cE '{_STOP_SELF_SENTINEL}|VLLM::Worker'); "
        "if [ -z \"$main_pids\" ] && [ -z \"$gpu_hit\" ] && [ \"${worker_cnt:-0}\" = 0 ]; then echo CLEAN; else echo BUSY; fi"
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
        # A slow cold start (the console PTY delivers the start command late,
        # or the process takes a moment to spawn) can miss the first probe;
        # re-check once before declaring a crash, so a slow start does not
        # burn a spurious crash event toward the watchdog breaker
        # (audit 2026-10-20 P2-9).
        await asyncio.sleep(_ms._START_PROBE_DELAY)
        model = _ms.state_machine.get_model(model_id)
        if not model or model.status != ModelStatus.LOADING:
            return
        present = await _ms._check_vllm_present_strict(api_host, api_port)
    if present is False:
        # Audit 2026-10-22 BCK-5: a manual stop in flight removes the
        # process on purpose — skip the rollback/crash path entirely. If
        # the stop has ALREADY finished, the status re-check above caught
        # it (stop flips LOADING→STOPPED before clearing the flag).
        if model_id in _stopping_models:
            return
        logger.warning(
            f"No vLLM process detected ~{2 * _ms._START_PROBE_DELAY:.0f}s after start "
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

    # Audit 2026-10-22 BCK-2: refresh=True bypasses the cache and runs a
    # full SSH round in this thread — bound it with the event-loop wall
    # clock so a wedged round cannot stall the start path indefinitely.
    try:
        ssh = await asyncio.wait_for(
            asyncio.to_thread(_ms.check_ssh_connectivity, True),
            timeout=_ms.settings.SSH_CMD_TIMEOUT * 4,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="AI server unreachable via SSH — the connectivity probe itself timed out"
        )
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

    # Audit 2026-10-22 BCK-5: the flag must cover the whole stop (kill +
    # verify-wait) so the start probe never sees the "process gone" window
    # of an intentional stop.
    _stopping_models.add(model_id)
    try:
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
            # A manual stop is a deliberate user action, not a crash:
            # drop any pending watchdog crash history (and suspension) so
            # a locked model is NOT re-launched right after the user
            # stopped it (incident 2026-10-22 stop↔auto-restart loop).
            _ms._watchdog_clear(model_id)

        await _unset_launch_env_vars(model_id)
    finally:
        _stopping_models.discard(model_id)

    result = {"status": "success" if stopped else "timeout", "stopped": stopped}
    if not stopped and stop_result.get("error"):
        result["error"] = stop_result["error"]
    return result
