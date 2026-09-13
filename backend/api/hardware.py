import asyncio
import logging
import os
import threading
import time
from typing import Any

from fastapi import APIRouter

from ..config.remote_client import config_manager
from ..executor.remote_executor import get_remote_executor
from ..monitor.gpu_metrics import map_nvidia_smi_gpus
from ..monitor.gpu_monitor import GPUMonitor
from ..schemas.hardware import GPUInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hardware", tags=["hardware"])

_gpu_monitor = None
_gpu_monitor_lock = threading.Lock()

def get_gpu_monitor():
    global _gpu_monitor
    if _gpu_monitor is None:
        with _gpu_monitor_lock:
            if _gpu_monitor is None:
                try:
                    _gpu_monitor = GPUMonitor()
                    _gpu_monitor.start()
                except Exception as e:
                    logger.warning(f"Local GPU monitor init failed (expected on client machines): {e}")
                    _gpu_monitor = None
    return _gpu_monitor

def get_gpu_info_local() -> list[GPUInfo]:
    monitor = get_gpu_monitor()
    if monitor is None:
        return []
    monitor.update_all_gpus()
    return monitor.gpu_info

def get_gpu_info_remote() -> list[GPUInfo]:
    executor = get_remote_executor()
    result = executor.get_nvidia_smi()

    if result.get("error"):
        logger.error(f"SSH nvidia-smi failed: {result['error']}")
        return []

    return [GPUInfo(**g) for g in map_nvidia_smi_gpus(result.get("gpus", []))]

def get_cpu_info_local() -> dict[str, Any]:
    try:
        import platform

        import psutil
        cpu_count = psutil.cpu_count(logical=True) or 0
        cpu_usage = psutil.cpu_percent(interval=0.1)
        cpu_model = "Unknown"

        try:
            if os.name == "nt":
                model = platform.processor()
                if model:
                    cpu_model = model
            else:
                with open("/proc/cpuinfo", encoding="utf-8") as f:
                    for line in f:
                        if line.lower().startswith("model name"):
                            cpu_model = line.split(":", 1)[1].strip()
                            break
        except Exception:
            pass

        return {
            "model": cpu_model,
            "count": cpu_count,
            "usage": cpu_usage
        }
    except Exception:
        return {"model": "Unknown", "count": 0, "usage": 0.0}

def get_memory_info_local() -> dict[str, Any]:
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {
            "total": mem.total,
            "used": mem.used,
            "free": mem.available,
            "usage": mem.percent
        }
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "usage": 0.0}

def get_disk_info_local() -> dict[str, Any]:
    try:
        import psutil
        if os.name == "nt":
            disk_path = os.path.splitdrive(os.getcwd())[0] + "\\"
        else:
            disk_path = "/"
        disk = psutil.disk_usage(disk_path)
        return {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "usage": disk.percent
        }
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "usage": 0.0}

@router.get("/metrics")
async def get_hardware_metrics():
    config = config_manager.get_config()

    if config.is_remote():
        executor = get_remote_executor()
        sys_info = await asyncio.to_thread(executor.get_system_info_combined)
        gpus = await asyncio.to_thread(get_gpu_info_remote)
        cpu_info = sys_info.get("cpu")
        memory_info = sys_info.get("memory")
        disk_info = sys_info.get("disk")
    else:
        gpus = await asyncio.to_thread(get_gpu_info_local)
        cpu_info = await asyncio.to_thread(get_cpu_info_local)
        memory_info = await asyncio.to_thread(get_memory_info_local)
        disk_info = await asyncio.to_thread(get_disk_info_local)

    return {
        "gpus": gpus,
        "cpu": cpu_info,
        "memory": memory_info,
        "disk": disk_info,
        "timestamp": time.time()
    }

@router.get("/software")
async def get_software_info():
    config = config_manager.get_config()
    if config.is_remote():
        executor = get_remote_executor()
        return await asyncio.to_thread(executor.get_software_info)
    return {
        "os": "Unknown", "gpu_driver": "Unknown",
        "cuda_toolkit": "Unknown", "vllm": "Unknown",
    }
