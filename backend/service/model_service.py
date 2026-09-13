import asyncio
import logging
import posixpath
import re
import shlex
import socket
import threading
import time
from datetime import datetime

from fastapi import HTTPException

from ..config.model_launch_config import launch_config_manager
from ..config.remote_client import config_manager, get_auth_headers, get_http_client
from ..config.settings import settings
from ..controller.state_machine import StateMachine
from ..executor.remote.process_ops import (
    _STOP_SELF_SENTINEL,
    _VLLM_MAIN_PATTERN,
    _ps_filter_self,
)
from ..executor.remote.ssh_probe import check_ssh_connectivity
from ..executor.remote_executor import get_remote_executor
from ..schemas.model import ModelInfo, ModelStatus

logger = logging.getLogger(__name__)

state_machine = StateMachine()

_ENV_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

def _parse_env_vars(env_vars: str) -> list[tuple[str, str]]:
    parsed: list[list[str]] = []
    for raw_line in env_vars.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            raise ValueError(f"unparseable env line: {line!r}")
        tokens = [t for t in tokens if t != "export"]
        current: int | None = None
        for token in tokens:
            if "=" in token:
                key, _sep, value = token.partition("=")
                if not _ENV_KEY_RE.match(key):
                    raise ValueError(f"expected KEY=VALUE: {line!r}")
                parsed.append([key, value])
                current = len(parsed) - 1
            else:
                if current is None:
                    raise ValueError(f"expected KEY=VALUE: {line!r}")
                prev_val = parsed[current][1]
                parsed[current][1] = f"{prev_val} {token}".strip() if prev_val else token
    return [(k, v) for k, v in parsed]

LOADING_GRACE_SECONDS = 120

def _loading_reset_allowed(model: ModelInfo, now: datetime | None = None,
                           grace_seconds: float = LOADING_GRACE_SECONDS) -> bool:
    if model.loading_since is None:
        return True
    if now is None:
        now = datetime.now()
    return (now - model.loading_since).total_seconds() >= grace_seconds

LOADING_HARD_CAP_SECONDS = 1800

def _loading_hard_reset_allowed(model: ModelInfo, now: datetime | None = None,
                                hard_cap_seconds: float = LOADING_HARD_CAP_SECONDS) -> bool:
    if model.loading_since is None:
        return False
    if now is None:
        now = datetime.now()
    return (now - model.loading_since).total_seconds() >= hard_cap_seconds

_PROBE_TCP_TIMEOUT = 3.0

async def _check_vllm_present_strict(api_host: str, api_port: int) -> bool | None:
    probe_host = api_host if api_host not in ("", "localhost") else "127.0.0.1"
    try:
        probe_sock = await asyncio.to_thread(
            socket.create_connection, (probe_host, api_port), _PROBE_TCP_TIMEOUT
        )
        probe_sock.close()
        return True
    except OSError:
        pass
    try:
        executor = get_remote_executor()
        def _probe_ps():
            return executor.execute(_ps_filter_self(_VLLM_MAIN_PATTERN))
        result = await asyncio.to_thread(_probe_ps)
        if not result["success"]:
            return None
        return bool(result["stdout"].strip())
    except Exception as e:
        logger.warning(f"vLLM presence probe failed (treating as unknown): {e}")
        return None

async def _sync_status_once(timeout: float, track_current: bool = False,
                            warn_unreachable: bool = False) -> int:
    config = config_manager.get_config()
    client = get_http_client()
    auth_headers = get_auth_headers(config)
    api_reachable = True
    try:
        response = await client.get(
            config.get_api_url('/v1/models'),
            timeout=timeout,
            headers=auth_headers,
        )
        response.raise_for_status()
        remote_models = response.json().get("data", [])
    except Exception as e:
        api_reachable = False
        remote_models = []
        if warn_unreachable:
            logger.warning(f"Remote vLLM API unreachable, model status may be stale: {e}")
    remote_model_ids = {m.get('id') for m in remote_models if m.get('id')}
    models = state_machine.get_all_models()

    has_loading = any(m.status == ModelStatus.LOADING for m in models)
    has_running = any(m.status == ModelStatus.RUNNING for m in models)
    vllm_process_exists = True
    strict_present: bool | None = None
    if (has_loading or has_running) and not remote_model_ids:
        strict_present = await _check_vllm_present_strict(config.host, config.port)
        vllm_process_exists = (strict_present is True)

    changed = 0
    for model in models:
        is_running = _match_model_name(model, remote_model_ids)

        if model.status in (ModelStatus.LOADING, ModelStatus.DOWNLOADED, ModelStatus.FAILED):
            if is_running:
                state_machine.update_model_status(model.id, ModelStatus.RUNNING)
                if track_current:
                    state_machine.set_current_model(model.id)
                changed += 1
            elif model.status == ModelStatus.LOADING and not vllm_process_exists:
                if _loading_reset_allowed(model):
                    state_machine.update_model_status(model.id, ModelStatus.STOPPED)
                    state_machine.clear_current_model(model.id)
                    changed += 1
            elif (model.status == ModelStatus.LOADING
                  and _loading_hard_reset_allowed(model)):
                logger.warning(
                    f"Model {model.id} stuck in LOADING for "
                    f">{LOADING_HARD_CAP_SECONDS // 60} min with a vLLM "
                    f"process present but never serving — resetting to "
                    f"STOPPED (possible port conflict with external vLLM)")
                state_machine.update_model_status(model.id, ModelStatus.STOPPED)
                state_machine.clear_current_model(model.id)
                changed += 1
        elif model.status == ModelStatus.RUNNING:
            if (api_reachable or strict_present is False) and not is_running:
                state_machine.update_model_status(model.id, ModelStatus.STOPPED)
                state_machine.clear_current_model(model.id)
                changed += 1
        elif model.status == ModelStatus.STOPPED:
            if is_running:
                state_machine.update_model_status(model.id, ModelStatus.RUNNING)
                if track_current:
                    state_machine.set_current_model(model.id)
                changed += 1
    return changed

def _match_served_id(model: ModelInfo, remote_ids: set[str]) -> str | None:
    model_dir_name = model.path.split("/")[-1] if "/" in model.path else model.path

    if model.path in remote_ids:
        return model.path
    if model.name in remote_ids:
        return model.name

    normalized_map: dict[str, str] = {}
    for rid in remote_ids:
        normalized_map.setdefault(rid.replace(".", "_").replace("-", "_"), rid)
    normalized_local = model_dir_name.replace(".", "_").replace("-", "_")
    if normalized_local in normalized_map:
        return normalized_map[normalized_local]

    if len(model_dir_name) >= 6:
        for rid in remote_ids:
            if model.path.endswith(rid) or rid.endswith(model_dir_name):
                return rid

    return None

def _match_model_name(model: ModelInfo, remote_ids: set[str]) -> bool:
    return _match_served_id(model, remote_ids) is not None

_DL_PROBE_SENTINEL = "VDB_DL_PROBE_SENTINEL"
_DL_PROCESS_PATTERN = r"hf download|huggingface-cli download"

def _build_download_status_command(log_file: str) -> str:
    safe_log = shlex.quote(log_file)
    dl_pids = (
        "ps -eo pid=,args= 2>/dev/null "
        f"| grep -v '{_DL_PROBE_SENTINEL}' "
        f"| grep -E '{_DL_PROBE_SENTINEL}|{_DL_PROCESS_PATTERN}' "
        "| awk '{print $1}' "
        "| head -1"
    )
    return (
        f"if [ -f {safe_log} ]; then "
        f"echo EXISTS; "
        f"echo \"SIZE=$(wc -c < {safe_log} 2>/dev/null)\"; "
        f"echo \"MTIME=$(stat -c %Y {safe_log} 2>/dev/null)\"; "
        f"echo \"PID=$({dl_pids})\"; "
        f"echo '---TAIL---'; "
        f"tail -50 {safe_log}; "
        f"else echo NOT_FOUND; fi"
    )

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
    poll_interval = 1
    elapsed = 0
    cmd = _build_stop_wait_command(model_path)
    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        check = await asyncio.to_thread(executor.execute, cmd)
        if check["success"] and "CLEAN" in check["stdout"]:
            return True
    return False

BENCHMARK_PROMPT = ("The quick brown fox jumps over the lazy dog. "
                    "This is a test of the model's generation speed.")
BENCHMARK_MAX_TOKENS = 128

_benchmark_inflight: set[str] = set()
_benchmark_inflight_lock = threading.Lock()

def _benchmark_acquire(key: str) -> bool:
    with _benchmark_inflight_lock:
        if key in _benchmark_inflight:
            return False
        _benchmark_inflight.add(key)
        return True

def _benchmark_release(key: str) -> None:
    with _benchmark_inflight_lock:
        _benchmark_inflight.discard(key)

async def _benchmark_precheck(model_id: str) -> tuple:
    model = state_machine.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    import httpx

    config = config_manager.get_config()
    client = get_http_client()
    auth_headers = get_auth_headers(config)
    try:
        resp = await client.get(config.get_api_url('/v1/models'), timeout=10.0, headers=auth_headers)
        resp.raise_for_status()
        remote_models = resp.json().get("data", [])
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"vLLM API 不可达: {e}")
    if not remote_models:
        raise HTTPException(status_code=400, detail="vLLM 服务没有已加载的模型")

    remote_ids = {m.get("id") for m in remote_models if m.get("id")}
    vllm_model_id = _match_served_id(model, remote_ids)
    if vllm_model_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_id}' is not loaded on vLLM. Loaded models: {sorted(remote_ids)}"
        )
    return client, auth_headers, vllm_model_id

def _model_delete_target(resolved_path: str) -> str:
    return re.sub(r"/snapshots/[^/]+/?$", "", resolved_path)

async def list_models() -> list[ModelInfo]:
    try:
        await _sync_status_once(timeout=3.0, warn_unreachable=True)
        return state_machine.get_all_models()
    except Exception as e:
        logger.warning(f"Auto status sync failed in list_models: {e}")
        return state_machine.get_all_models()

async def sync_status() -> int:
    return await _sync_status_once(
        timeout=settings.STATUS_SYNC_TIMEOUT, track_current=True
    )

def scan_models(model_dir: str) -> dict:
    executor = get_remote_executor()
    custom_dir = config_manager.get_config().model_save_path or ""
    result = executor.scan_models(model_dir, custom_dir=custom_dir)

    if not result["success"]:
        return {"success": False, "error": result.get("error", "Scan failed")}

    for model in result["models"]:
        model_id = model["name"]
        existing_model = state_machine.get_model(model_id)

        try:
            saved_config = launch_config_manager.load_config(model_id)
            if saved_config:
                saved_config.pop("_model_id", None)
                config = {
                    "name": model["name"],
                    "path": model["path"],
                    **saved_config,
                }
            else:
                config = {
                    "name": model["name"],
                    "path": model["path"]
                }

            if not existing_model:
                state_machine.add_model(
                    model_id=model_id,
                    name=model["name"],
                    path=model["path"],
                    config=config,
                    size_bytes=model.get("size_bytes", 0)
                )
                state_machine.update_model_status(model_id, ModelStatus.DOWNLOADED)
            else:
                if saved_config:
                    full_config = {
                        "name": model["name"],
                        "path": model["path"],
                        **saved_config,
                    }
                    state_machine.replace_model_config(model_id, full_config)
                else:
                    state_machine.update_model_config(model_id, config)
                if model.get("size_bytes"):
                    state_machine.update_model_size(model_id, model["size_bytes"])
        except Exception as e:
            logger.warning(f"Failed to add/update model {model_id}: {e}")

    removed_stale = []
    try:
        checkable = [
            m for m in state_machine.get_all_models()
            if m.status not in (ModelStatus.RUNNING, ModelStatus.LOADING)
        ]
        if checkable:
            existence = executor.check_models_existence([m.path for m in checkable])
            for m in checkable:
                if existence.get(m.path) is False:
                    logger.info(f"移除陈旧模型条目 {m.id}（路径已不存在: {m.path}）")
                    if state_machine.remove_model(m.id):
                        removed_stale.append(m.id)
    except Exception as e:
        logger.warning(f"Stale model reconciliation failed: {e}")

    return {
        "success": True,
        "models": result["models"],
        "count": result["count"],
        "removed_stale": removed_stale
    }

async def start_model(model_id: str) -> dict:
    model = state_machine.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    if model.status in (ModelStatus.RUNNING, ModelStatus.LOADING):
        raise HTTPException(status_code=409, detail=f"Model is already {model.status.value}")

    ssh = await asyncio.to_thread(check_ssh_connectivity, True)
    if not ssh.get("connected"):
        raise HTTPException(
            status_code=503,
            detail=f"AI server unreachable via SSH — model start requires SSH ({ssh.get('error', 'unknown error')})"
        )

    config = launch_config_manager.load_config(model_id)
    if not config or not config.get("start_command"):
        raise HTTPException(status_code=400, detail="Start command not configured")

    try:
        parsed_env = _parse_env_vars(config.get("env_vars", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid environment variables: {e}")

    state_machine.update_model_status(model_id, ModelStatus.LOADING)

    executor = get_remote_executor()
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

    return {
        "status": "loading",
        "model_id": model_id,
        "command": full_command
    }

def get_download_status(log_file: str) -> dict:
    executor = get_remote_executor()
    cmd = _build_download_status_command(log_file)
    result = executor.execute(cmd)
    if not result["success"]:
        return {
            "status": "unknown",
            "log": "",
            "message": "Failed to read download log"
        }

    stdout = result["stdout"]
    if stdout.startswith("NOT_FOUND"):
        return {
            "status": "not_found",
            "log": "",
            "message": "Download log file not found"
        }

    head, _sep, log_content = stdout.partition("---TAIL---\n")
    log_size = 0
    log_mtime = 0
    pid = ""
    for line in head.splitlines():
        if line.startswith("SIZE="):
            v = line[5:].strip()
            log_size = int(v) if v.isdigit() else 0
        elif line.startswith("MTIME="):
            v = line[6:].strip()
            log_mtime = int(v) if v.isdigit() else 0
        elif line.startswith("PID="):
            pid = line[4:].strip()
    is_running = pid.isdigit()

    has_fatal_error = False
    for line in log_content.split("\n"):
        if ("ERROR: hf not found" in line or
            "ERROR: huggingface-cli not found" in line or
            "command not found" in line or
            "Traceback" in line or "Exception" in line or
            "OSError" in line or "ConnectionError" in line or
            "is deprecated and no longer works" in line):
            has_fatal_error = True
            break

    if re.search(r'/models--[\w.()-]+/snapshots/[\w]+', log_content):
        return {
            "status": "complete",
            "log": log_content,
            "message": "Download complete"
        }

    progress_matches = re.findall(r'(\d+(?:\.\d+)?)\s*%', log_content)
    parsed_progress = float(progress_matches[-1]) if progress_matches else None

    if is_running:
        if parsed_progress is not None:
            progress = min(99, parsed_progress)
        elif log_mtime > 0:
            elapsed = max(1, time.time() - log_mtime)
            progress = min(95, max(5, (elapsed / 1800) * 100))
        else:
            progress = 5
        return {
            "status": "downloading",
            "progress": round(progress, 1),
            "log": log_content[-500:],
            "message": f"Downloading... ({log_size / 1024:.1f}KB)"
        }

    if has_fatal_error:
        reason_lines = [
            line.strip() for line in log_content.split("\n")
            if ("ERROR" in line or "Traceback" in line or "Exception" in line
                    or "command not found" in line or "OSError" in line
                    or "ConnectionError" in line or "not found" in line)
        ]
        reason = " | ".join(reason_lines[-3:])[:500]
        return {
            "status": "failed",
            "log": log_content,
            "message": "Download failed",
            "reason": reason
        }

    return {
        "status": "complete",
        "log": log_content,
        "message": "Download complete"
    }

async def stop_model(model_id: str) -> dict:
    executor = get_remote_executor()

    model = state_machine.get_model(model_id)
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

    model = state_machine.get_model(model_id)
    if model and stopped:
        state_machine.update_model_status(model_id, ModelStatus.STOPPED)
        state_machine.clear_current_model(model_id)

    result = {"status": "success" if stopped else "timeout", "stopped": stopped}
    if not stopped and stop_result.get("error"):
        result["error"] = stop_result["error"]
    return result

async def benchmark_model(model_id: str) -> dict:
    import httpx

    client, auth_headers, vllm_model_id = await _benchmark_precheck(model_id)
    if not _benchmark_acquire(vllm_model_id):
        raise HTTPException(status_code=409,
                             detail=f"A benchmark is already running for model '{vllm_model_id}'")
    try:
        url = config_manager.get_config().get_api_url('/v1/completions')
        timeout = settings.BENCHMARK_TIMEOUT
        payload = {
            "model": vllm_model_id,
            "prompt": BENCHMARK_PROMPT,
            "max_tokens": BENCHMARK_MAX_TOKENS,
            "temperature": 0,
            "ignore_eos": True,
            "stream": False,
        }
        start = time.perf_counter()
        try:
            response = await client.post(url, json=payload, timeout=timeout, headers=auth_headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                payload = {k: v for k, v in payload.items() if k != "ignore_eos"}
                start = time.perf_counter()
                try:
                    response = await client.post(url, json=payload, timeout=timeout,
                                                 headers=auth_headers)
                    response.raise_for_status()
                except httpx.HTTPError as e2:
                    raise HTTPException(status_code=502,
                                        detail=f"测速请求失败: {e2}") from e2
            else:
                raise HTTPException(status_code=502,
                                    detail=f"测速请求失败: {e}") from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502,
                                detail=f"测速请求失败: {e}") from e
        elapsed = time.perf_counter() - start
        try:
            data = response.json()
        except ValueError as e:
            raise HTTPException(status_code=502,
                                detail=f"vLLM 返回非 JSON 响应: {e}") from e
        usage = data.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        tokens_per_second = (round(completion_tokens / elapsed, 1)
                             if elapsed > 0 and completion_tokens > 0 else 0.0)
        return {
            "success": True,
            "model_id": model_id,
            "vllm_model_id": vllm_model_id,
            "tokens_per_second": tokens_per_second,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "elapsed_seconds": round(elapsed, 2),
        }
    finally:
        _benchmark_release(vllm_model_id)

def download_model(model_repo: str, model_save_path: str, hf_mirror: bool) -> dict:
    executor = get_remote_executor()

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
    executor = get_remote_executor()
    return executor.check_cli_tools()

def install_cli_tool(tool: str) -> dict:
    executor = get_remote_executor()
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
    executor = get_remote_executor()
    return executor.get_install_status(tool, log_file)

def ensure_hf_mirror() -> dict:
    executor = get_remote_executor()
    result = executor.ensure_hf_mirror()

    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to configure HF mirror"))

    return result

def get_model(model_id: str) -> ModelInfo:
    model = state_machine.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return model

def delete_model(model_id: str) -> dict:
    model = state_machine.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    executor = get_remote_executor()
    if model.status in (ModelStatus.RUNNING, ModelStatus.LOADING):
        executor.stop_vllm(force=True, model_path=model.path)
        time.sleep(1)

    model_path = model.path
    normalized = posixpath.normpath(model_path)
    logger.info(f"Delete model {model_id}: path={model_path}, normalized={normalized}")

    dangerous_paths_exact = {"/", "/etc", "/usr", "/var", "/home", "/root", "/opt", "/tmp", "/bin", "/sbin", "/lib", "/boot", "/dev", "/proc", "/sys"}
    safe_model_prefixes = ("/opt/models/", "/home/", "/root/")

    def _assert_deletable(target: str, label: str) -> None:
        if not target.startswith("/"):
            raise HTTPException(status_code=400, detail=f"Refusing to delete non-absolute path: {label}={target}")
        if target in dangerous_paths_exact:
            raise HTTPException(status_code=400, detail=f"Refusing to delete dangerous path: {label}={target}")
        if not target.startswith(safe_model_prefixes):
            raise HTTPException(status_code=400, detail=f"Refusing to delete path outside model directories: {label}={target}")
        min_depth = 3 if target.startswith("/home/") else 2
        if target.count("/") < min_depth:
            raise HTTPException(status_code=400, detail=f"Model path not deep enough: {label}={target}")

    _assert_deletable(normalized, "path")

    safe_path = shlex.quote(normalized)
    readlink_result = executor.execute(f"readlink -f {safe_path}")
    real_path = readlink_result["stdout"].strip() if readlink_result["success"] else normalized
    if real_path:
        _assert_deletable(real_path, "resolved_path")

    delete_target = _model_delete_target(real_path)
    _assert_deletable(delete_target, "delete_target")
    logger.info(f"Delete model {model_id}: resolved={real_path}, delete_target={delete_target}")

    result = executor.execute(f"rm -rf {shlex.quote(delete_target)} {safe_path} 2>/dev/null")
    if not result["success"]:
        logger.error(f"Delete failed for {model_id} (path={model_path}): {result.get('stderr', '')}")
        raise HTTPException(status_code=500, detail="Failed to delete remote files, see server logs for details")

    verify_cmd = (
        f"test -e {shlex.quote(delete_target)} || test -L {shlex.quote(delete_target)} && echo REMAINS; "
        f"test -e {safe_path} || test -L {safe_path} && echo REMAINS; echo DONE"
    )
    verify_result = executor.execute(verify_cmd)
    if not verify_result["success"]:
        logger.error(f"Delete verification failed for {model_id} (remote unreachable)")
        raise HTTPException(
            status_code=500,
            detail="Delete verification failed (remote unreachable) — please confirm manually")
    if "REMAINS" in verify_result["stdout"]:
        raise HTTPException(status_code=500, detail="Delete failed: remote model files still exist")

    if not state_machine.remove_model(model_id):
        raise HTTPException(status_code=404, detail="Model not found")

    launch_config_manager.delete_config(model_id)

    return {"status": "success"}
