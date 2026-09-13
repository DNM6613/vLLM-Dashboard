import logging
import posixpath
import re

from fastapi import APIRouter, HTTPException, Query

from ..config.model_launch_config import launch_config_manager
from ..config.server_config import has_parent_path_segment
from ..schemas.model import ModelInfo
from ..service import model_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/models", tags=["models"])

MAX_COMMAND_LENGTH = 2000

MAX_ENV_VARS_LENGTH = 2000

@router.get("")
async def list_models():
    return await model_service.list_models()

@router.post("/sync-status")
async def sync_model_status():
    try:
        synced_count = await model_service.sync_status()
        return {"synced": True, "synced_count": synced_count}
    except Exception as e:
        logger.warning(f"Status sync failed: {e}")
        return {"synced": False, "error": str(e)}

@router.post("/scan")
def scan_models(model_dir: str = ""):
    if model_dir and has_parent_path_segment(model_dir):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    result = model_service.scan_models(model_dir)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Scan failed"))

    return {
        "success": True,
        "models": result["models"],
        "model_dir": "",
        "count": result["count"],
        "removed_stale": result["removed_stale"]
    }

@router.post("/start")
async def start_model(model_id: str = Query(...)):
    return await model_service.start_model(model_id)

@router.get("/launch-config")
def get_launch_config(model_id: str):
    config = launch_config_manager.load_config(model_id)
    if config is None:
        return {"has_config": False, "config": None}
    return {"has_config": True, "config": config}

@router.post("/launch-config")
def save_launch_config(config: dict):
    model_id = config.get("model_id")
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")

    allowed_keys = {"model_id", "start_command", "env_vars"}
    extra_keys = set(config.keys()) - allowed_keys
    if extra_keys:
        raise HTTPException(status_code=400, detail=f"Unexpected fields: {extra_keys}")

    existing = launch_config_manager.load_config(model_id) if (
        config.get("start_command") is None or config.get("env_vars") is None) else None

    start_command = config.get("start_command")
    if start_command is None:
        start_command = existing.get("start_command", "") if existing else ""
    elif not isinstance(start_command, str):
        raise HTTPException(status_code=400, detail="start_command must be a string")
    elif len(start_command) > MAX_COMMAND_LENGTH:
        raise HTTPException(status_code=400, detail=f"start_command too long (max {MAX_COMMAND_LENGTH} chars)")

    env_vars = config.get("env_vars")
    if env_vars is None:
        env_vars = existing.get("env_vars", "") if existing else ""
    elif not isinstance(env_vars, str):
        raise HTTPException(status_code=400, detail="env_vars must be a string")
    elif len(env_vars) > MAX_ENV_VARS_LENGTH:
        raise HTTPException(status_code=400, detail=f"env_vars too long (max {MAX_ENV_VARS_LENGTH} chars)")

    safe_config = {"model_id": model_id, "start_command": start_command, "env_vars": env_vars}
    success = launch_config_manager.save_config(model_id, safe_config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save config")
    return {"success": True}

@router.get("/download/status")
def get_download_status(log_file: str = Query(...)):
    if not re.fullmatch(r'/tmp/model_download_[\w.\-:@~]+\.log', log_file):
        raise HTTPException(status_code=400, detail="Invalid log file path")
    if not posixpath.normpath(log_file).startswith('/tmp/model_download_'):
        raise HTTPException(status_code=400, detail="Invalid log file path")

    return model_service.get_download_status(log_file)

@router.post("/stop")
async def stop_model(model_id: str = Query(...)):
    return await model_service.stop_model(model_id)

@router.post("/benchmark")
async def benchmark_model(model_id: str = Query(...)):
    return await model_service.benchmark_model(model_id)

@router.post("/download")
def download_model(model_repo: str = Query(...), model_save_path: str = Query(""), hf_mirror: bool = Query(True)):
    if not re.fullmatch(r'[\w.\-:@~/]+', model_repo):
        raise HTTPException(status_code=400, detail="Invalid model_repo")
    if model_repo.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid model_repo: must not start with '-'")

    if model_save_path:
        if has_parent_path_segment(model_save_path) or len(model_save_path) > 512:
            raise HTTPException(status_code=400, detail="Invalid model_save_path")
        if not re.fullmatch(r'[\w/\-.~:@]+', model_save_path):
            raise HTTPException(status_code=400, detail="Invalid model_save_path")

    return model_service.download_model(model_repo, model_save_path, hf_mirror)

@router.get("/cli-status")
def check_cli_tools():
    return model_service.check_cli_tools()

@router.post("/install-cli")
def install_cli_tool(tool: str = Query(...)):
    if tool != "hf":
        raise HTTPException(status_code=400, detail="Invalid tool name, must be 'hf'")

    return model_service.install_cli_tool(tool)

@router.get("/install-status")
def get_install_status(tool: str = Query(...), log_file: str = Query(...)):
    return model_service.get_install_status(tool, log_file)

@router.post("/ensure-hf-mirror")
def ensure_hf_mirror():
    return model_service.ensure_hf_mirror()

@router.get("/{model_id:path}", response_model=ModelInfo | None)
def get_model(model_id: str):
    return model_service.get_model(model_id)

@router.delete("/{model_id:path}")
def delete_model(model_id: str):
    return model_service.delete_model(model_id)
