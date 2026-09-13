import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from ..config.remote_client import config_manager
from ..config.server_config import (
    CREDENTIAL_MASK,
    DetailedConnectionStatus,
    ServerConfig,
)
from ..executor.remote.ssh_probe import check_ssh_connectivity
from ..executor.remote_executor import get_remote_executor
from ..schemas.model import ModelStatus
from ..service.model_service import state_machine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/config", tags=["config"])

class ServerConfigUpdate(BaseModel):
    id: str | None = None
    host: str | None = None
    port: int | None = None
    api_key: str | None = None
    use_auth: bool | None = None
    ssh_port: int | None = None
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_key_path: str | None = None
    venv_name: str | None = None
    model_save_path: str | None = None
    bmc_host: str | None = None
    bmc_username: str | None = None
    bmc_password: str | None = None

_CREDENTIAL_FIELDS = ("ssh_password", "bmc_password", "api_key")

def _mask_credentials(config: ServerConfig) -> ServerConfig:
    return config.model_copy(update={
        f: (CREDENTIAL_MASK if getattr(config, f) else None)
        for f in _CREDENTIAL_FIELDS
    })

@router.get("/server", response_model=ServerConfig)
def get_server_config():
    config = config_manager.get_config()
    return _mask_credentials(config)

@router.post("/server")
def update_server_config(config_update: ServerConfigUpdate):
    update_data = config_update.model_dump(exclude_unset=True)
    for f in _CREDENTIAL_FIELDS:
        if update_data.get(f) == CREDENTIAL_MASK:
            del update_data[f]
    try:
        new_config = config_manager.merge_config(update_data)
    except ValidationError as e:
        fields = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg'].removeprefix('Value error, ')}"
            for err in e.errors()
        )
        raise HTTPException(status_code=400, detail=f"Invalid config field: {fields}") from e
    return {"status": "success", "config": _mask_credentials(new_config)}

@router.get("/server/status", response_model=DetailedConnectionStatus)
async def get_server_status(refresh: bool = False):
    return await config_manager.check_connection(refresh=refresh)

@router.get("/server/health")
async def get_server_health():
    try:
        health = await config_manager.get_health()
        return health
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@router.get("/ssh/status")
def get_ssh_status():
    return check_ssh_connectivity()

def _reset_models_for_power_off() -> int:
    reset = 0
    for model in state_machine.get_all_models():
        if model.status in (ModelStatus.RUNNING, ModelStatus.LOADING):
            state_machine.update_model_status(model.id, ModelStatus.STOPPED)
            state_machine.clear_current_model(model.id)
            reset += 1
    return reset

@router.post("/server/shutdown")
async def shutdown_server():
    executor = get_remote_executor()
    result = await asyncio.to_thread(executor.shutdown_server)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error") or "Shutdown command failed",
        )

    _reset_models_for_power_off()
    logger.info("Server shutdown (poweroff) command issued to %s", executor.host)
    return {"status": "success", "stopped": True}

@router.post("/server/poweron")
async def poweron_server():
    executor = get_remote_executor()
    result = await asyncio.to_thread(executor.power_on_server)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error") or "Power-on command failed",
        )

    logger.info("Server power-on (BMC IPMI) command issued")
    return {"status": "success", "powered_on": True}

@router.get("/server/bmc-status")
async def get_bmc_status():
    executor = get_remote_executor()
    return await asyncio.to_thread(executor.check_bmc_status)

@router.post("/server/bmc-reset")
async def bmc_power_off_reset():
    reset = _reset_models_for_power_off()
    if reset:
        logger.info("BMC power off reset requested: %d model(s) reset to STOPPED", reset)
    return {"status": "success", "reset": reset}
