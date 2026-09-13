import asyncio
import contextlib
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from ..api.hardware import get_gpu_monitor
from ..config.remote_client import config_manager
from ..executor.remote_executor import get_remote_executor
from ..middleware import ws_api_key_valid, ws_connections_total
from ..monitor.gpu_metrics import map_nvidia_smi_gpus
from ..monitor.model_monitor import _disconnected_payload as _model_disconnected_payload
from ..monitor.model_monitor import fetch_model_status

logger = logging.getLogger(__name__)

def _get_gpu_metrics_local():
    monitor = get_gpu_monitor()
    if monitor:
        return monitor.get_current_metrics()
    return []

def _get_hardware_snapshot():
    config = config_manager.get_config()
    if config.is_remote():
        try:
            executor = get_remote_executor()
            result = executor.get_hardware_snapshot()
        except Exception as e:
            logger.error(f"Failed to get remote hardware snapshot: {e}")
            return [], {"cpu": None, "memory": None, "disk": None}
        return map_nvidia_smi_gpus(result.get("gpus", [])), {
            "cpu": result.get("cpu"),
            "memory": result.get("memory"),
            "disk": result.get("disk"),
        }
    from ..api.hardware import get_cpu_info_local, get_disk_info_local, get_memory_info_local
    return _get_gpu_metrics_local(), {
        "cpu": get_cpu_info_local(),
        "memory": get_memory_info_local(),
        "disk": get_disk_info_local(),
    }

async def _fetch_model_status():
    try:
        return await fetch_model_status()
    except Exception as e:
        logger.error(f"Failed to fetch vLLM model status: {e}")
        return _model_disconnected_payload()

class HardwareBroadcaster:

    _POLL_INTERVAL = 0.5
    _EMPTY_EXTRA_DELAY = 4.0
    _BROADCAST_SEND_TIMEOUT = 3.0

    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._poll_task: asyncio.Task | None = None

    def register(self, websocket: WebSocket) -> None:
        self._clients.add(websocket)
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.get_running_loop().create_task(self._poll_loop())

    def unregister(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def _poll_loop(self) -> None:
        while self._clients:
            gpus, sys_metrics = await asyncio.to_thread(_get_hardware_snapshot)
            model_status = await _fetch_model_status()
            data = {
                "gpus": gpus,
                "cpu": sys_metrics.get("cpu"),
                "memory": sys_metrics.get("memory"),
                "disk": sys_metrics.get("disk"),
                "model": model_status,
                "timestamp": time.time(),
            }
            if not gpus:
                data["error"] = "Failed to get GPU data, check server connection and NVIDIA driver"
            await self._broadcast(data)
            await asyncio.sleep(
                self._POLL_INTERVAL + (self._EMPTY_EXTRA_DELAY if not gpus else 0.0))

    async def _broadcast(self, data: dict) -> None:
        if not self._clients:
            return
        message = json.dumps({"type": "gpu_metrics", "data": data})
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send_text(message),
                                       timeout=self._BROADCAST_SEND_TIMEOUT)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def shutdown(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poll_task

hardware_broadcaster = HardwareBroadcaster()

async def websocket_hardware(websocket: WebSocket):
    if not ws_api_key_valid(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ws_connections_total.labels(ws="hardware").inc()
    hardware_broadcaster.register(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hardware_broadcaster.unregister(websocket)
