import asyncio
import contextlib
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from ..api.hardware import get_gpu_monitor
from ..config.remote_client import config_manager
from ..config.settings import settings
from ..executor.remote_executor import get_remote_executor
from ..middleware import ws_api_key_valid, ws_connections_total
from ..monitor.gpu_metrics import map_nvidia_smi_gpus
from ..remote_probe import RemoteProbeTimeout, probe_in_thread

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
            return map_nvidia_smi_gpus(result.get("gpus", [])), {
                "cpu": result.get("cpu"),
                "memory": result.get("memory"),
                "disk": result.get("disk"),
            }
        except Exception as e:
            logger.error(f"Failed to get remote hardware snapshot: {e}")
            return [], {"cpu": None, "memory": None, "disk": None}
    from ..api.hardware import get_cpu_info_local, get_disk_info_local, get_memory_info_local
    return _get_gpu_metrics_local(), {
        "cpu": get_cpu_info_local(),
        "memory": get_memory_info_local(),
        "disk": get_disk_info_local(),
    }

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
            try:
                # Wall-clock bound: a stalled remote SSH read must not pin this
                # loop (or a worker thread per cycle) — on timeout fall back to
                # the degraded payload path below.
                try:
                    gpus, sys_metrics = await probe_in_thread(
                        _get_hardware_snapshot,
                        settings.HARDWARE_PROBE_TIMEOUT, op="hardware snapshot")
                except RemoteProbeTimeout:
                    gpus, sys_metrics = [], {"cpu": None, "memory": None, "disk": None}
                data = {
                    "gpus": gpus,
                    "cpu": sys_metrics.get("cpu"),
                    "memory": sys_metrics.get("memory"),
                    "disk": sys_metrics.get("disk"),
                    "timestamp": time.time(),
                }
                if not gpus:
                    data["error"] = "Failed to get GPU data, check server connection and NVIDIA driver"
                await self._broadcast(data)
                await asyncio.sleep(
                    self._POLL_INTERVAL + (self._EMPTY_EXTRA_DELAY if not gpus else 0.0))
            except Exception as e:
                # One broken cycle (bad mapping data, send failure, ...) must
                # not kill the broadcast task — log and keep polling.
                logger.error(f"Hardware broadcast cycle failed: {e}")
                await asyncio.sleep(self._POLL_INTERVAL)

    async def _broadcast(self, data: dict) -> None:
        if not self._clients:
            return
        message = json.dumps({"type": "gpu_metrics", "data": data})

        async def _send(ws: WebSocket) -> None:
            await asyncio.wait_for(ws.send_text(message),
                                   timeout=self._BROADCAST_SEND_TIMEOUT)

        # Concurrent sends (audit 2026-10-20 P2-8): N slow/half-open
        # subscribers must not serially stretch the 0.5 s cycle to 3N s —
        # every subscriber's data would lag by that much.
        clients = list(self._clients)
        results = await asyncio.gather(
            *(_send(ws) for ws in clients), return_exceptions=True)
        for ws, r in zip(clients, results):
            if isinstance(r, Exception):
                self._clients.discard(ws)

    async def shutdown(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            # Audit 2026-10-22 BCK-9: only the expected CancelledError is
            # swallowed — the old suppress(CancelledError, Exception) hid real
            # shutdown-path bugs without a trace.
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Hardware broadcaster: poll task failed to stop cleanly")

hardware_broadcaster = HardwareBroadcaster()

# Application-level keepalive: starlette exposes no WS ping, and the
# client->server direction is idle by design — without a heartbeat, middle
# boxes that drop long-idle TCP connections leave both ends hanging.
_KEEPALIVE_TIMEOUT = 60.0
# Audit 2026-10-22 BCK-7: the keepalive SEND must be wall-clock bounded too.
# A client that dies hard (power loss, no RST/FIN) leaves a half-open socket;
# the receive times out, then an unbounded send_text can block until the TCP
# stack gives up (minutes) — pinning this handler so the finally-unregister
# never runs and the zombie stays in the broadcaster's client set. A bounded
# send that times out means the connection is dead: close it so unregister
# runs and the broadcaster stops paying a 0.5 s cycle to a corpse.
_KEEPALIVE_SEND_TIMEOUT = 5.0

async def websocket_hardware(websocket: WebSocket):
    if not ws_api_key_valid(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ws_connections_total.labels(ws="hardware").inc()
    hardware_broadcaster.register(websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(),
                                       timeout=_KEEPALIVE_TIMEOUT)
            except TimeoutError:
                # Nudge the client (the UI ignores unknown message types);
                # a dead socket surfaces on the next receive.
                try:
                    await asyncio.wait_for(
                        websocket.send_text(json.dumps({"type": "keepalive"})),
                        timeout=_KEEPALIVE_SEND_TIMEOUT)
                except Exception:
                    # Keepalive send hung or failed — the connection is dead;
                    # close so the finally unregisters it (no zombie
                    # subscriber). The broadcaster's bounded broadcast would
                    # also evict it, but the handler must not stay pinned.
                    with contextlib.suppress(Exception):
                        await websocket.close()
                    return
    except WebSocketDisconnect:
        pass
    finally:
        hardware_broadcaster.unregister(websocket)
