from fastapi import APIRouter

from ..monitor.model_monitor import fetch_model_status

router = APIRouter(prefix="/api/v1/models", tags=["model-status"])

@router.get("/runtime-status")
async def get_model_runtime_status():
    return await fetch_model_status()
