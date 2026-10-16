"""Model catalog: scan, get, delete (with remote path safety interlocks).

Split out of ``model_service.py`` (file-size gate). Shared state and patch
hooks live on the ``model_service`` facade and are resolved through it.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import re
import shlex

from fastapi import HTTPException

from ..config.model_launch_config import launch_config_manager
from ..config.remote_client import config_manager
from ..executor.remote_executor import get_remote_executor
from ..schemas.model import ModelInfo, ModelStatus
from . import model_service as _ms

logger = logging.getLogger(__name__)


def _model_delete_target(resolved_path: str) -> str:
    return re.sub(r"/snapshots/[^/]+/?$", "", resolved_path)


def scan_models(model_dir: str) -> dict:
    executor = get_remote_executor()
    custom_dir = config_manager.get_config().model_save_path or ""
    result = executor.scan_models(model_dir, custom_dir=custom_dir)

    if not result["success"]:
        return {"success": False, "error": result.get("error", "Scan failed")}

    for model in result["models"]:
        model_id = model["name"]
        existing_model = _ms.state_machine.get_model(model_id)

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
                _ms.state_machine.add_model(
                    model_id=model_id,
                    name=model["name"],
                    path=model["path"],
                    config=config,
                    size_bytes=model.get("size_bytes", 0)
                )
                _ms.state_machine.update_model_status(model_id, ModelStatus.DOWNLOADED)
            else:
                if saved_config:
                    full_config = {
                        "name": model["name"],
                        "path": model["path"],
                        **saved_config,
                    }
                    _ms.state_machine.replace_model_config(model_id, full_config)
                else:
                    _ms.state_machine.update_model_config(model_id, config)
                if model.get("size_bytes"):
                    _ms.state_machine.update_model_size(model_id, model["size_bytes"])
        except Exception as e:
            logger.warning(f"Failed to add/update model {model_id}: {e}")

    removed_stale = []
    try:
        checkable = [
            m for m in _ms.state_machine.get_all_models()
            if m.status not in (ModelStatus.RUNNING, ModelStatus.LOADING)
        ]
        if checkable:
            existence = executor.check_models_existence([m.path for m in checkable])
            for m in checkable:
                if existence.get(m.path) is False:
                    logger.info(f"移除陈旧模型条目 {m.id}（路径已不存在: {m.path}）")
                    if _ms.state_machine.remove_model(m.id):
                        removed_stale.append(m.id)
                        _ms._remove_benchmark_result(m.id)
                        _ms._take_auto_bench_pending(m.id)
    except Exception as e:
        logger.warning(f"Stale model reconciliation failed: {e}")

    return {
        "success": True,
        "models": result["models"],
        "count": result["count"],
        "removed_stale": removed_stale
    }


def get_model(model_id: str) -> ModelInfo:
    model = _ms.state_machine.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return _ms._with_benchmark([model])[0]


async def delete_model(model_id: str) -> dict:
    model = _ms.state_machine.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    executor = get_remote_executor()
    if model.status in (ModelStatus.RUNNING, ModelStatus.LOADING):
        # Deleting while a live vLLM still holds the files is data loss —
        # verify the stop actually took effect before touching the path.
        stop_result = await asyncio.to_thread(
            executor.stop_vllm, force=True, model_path=model.path)
        wait_path = model.path if stop_result.get("mode") == "scoped" else ""
        stopped = await _ms._wait_for_remote_stop(executor, max_wait=5, model_path=wait_path)
        if not stopped:
            raise HTTPException(
                status_code=503,
                detail="vLLM 仍在运行 — 已中止删除，请先停止模型后重试")

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

    if not _ms.state_machine.remove_model(model_id):
        raise HTTPException(status_code=404, detail="Model not found")

    _ms._remove_benchmark_result(model_id)
    _ms._take_auto_bench_pending(model_id)
    launch_config_manager.delete_config(model_id)

    return {"status": "success"}
