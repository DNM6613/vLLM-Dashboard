"""ConfigManager connection / health probes (HTTP, SSH, TCP reach).

Split out of ``remote_client.py`` (file-size gate). Mixed into
``ConfigManager`` (see ``remote_client.py``); every method expects ``self``
to be the ConfigManager instance.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import socket
import time
from typing import Any

import httpx

from . import remote_client as _rc
from .server_config import ConnectionDetail, ServerStatus
from .settings import settings

logger = logging.getLogger(__name__)

HEALTH_REACH_PROBE_TIMEOUT = 2


class ConnectionProbeMixin:
    async def check_connection(self, refresh: bool = False):
        if refresh:
            self.invalidate_ssh_version()

        tasks = []

        if self.config.host:
            tasks.append(self._check_http())

        if self.config.host and self.config.ssh_username:
            tasks.append(self._check_ssh())

        results = await asyncio.gather(*tasks, return_exceptions=True)

        http_status = None
        ssh_status = None
        overall = ServerStatus.UNKNOWN

        for result in results:
            if isinstance(result, Exception):
                continue
            if isinstance(result, tuple):
                method, detail = result
                if method == "http":
                    http_status = detail
                elif method == "ssh":
                    ssh_status = detail

        if (http_status and http_status.status == ServerStatus.CONNECTED) or \
           (ssh_status and ssh_status.status == ServerStatus.CONNECTED):
            overall = ServerStatus.CONNECTED
        elif (http_status and http_status.status == ServerStatus.DISCONNECTED) or \
             (ssh_status and ssh_status.status == ServerStatus.DISCONNECTED):
            overall = ServerStatus.DISCONNECTED
        elif http_status or ssh_status:
            overall = ServerStatus.ERROR

        return _rc.DetailedConnectionStatus(
            http=http_status,
            ssh=ssh_status,
            overall=overall
        )

    async def _check_http(self) -> tuple[str, ConnectionDetail]:
        start = time.time()
        try:
            client = _rc.get_http_client()
            target = _rc.resolve_api_target(self.config)
            response = await client.get(
                f"{target.base_url}/v1/models",
                timeout=3.0,
                headers=target.headers,
            )

            if response.status_code == 200:
                latency_ms = round((time.time() - start) * 1000, 2)
                version = "unknown"
                try:
                    models_data = response.json()
                    model_list = models_data.get("data", [])
                    version = f"{len(model_list)} 个模型已加载" if model_list else "vLLM 运行中 (无模型)"
                except Exception:
                    pass

                return "http", ConnectionDetail(
                    status=ServerStatus.CONNECTED,
                    latency_ms=latency_ms,
                    version=version
                )
            else:
                return "http", ConnectionDetail(
                    status=ServerStatus.DISCONNECTED,
                    error=f"HTTP {response.status_code}"
                )
        except httpx.ConnectError:
            return "http", ConnectionDetail(
                status=ServerStatus.DISCONNECTED,
                error="vLLM 未启动或无法连接"
            )
        except httpx.TimeoutException:
            return "http", ConnectionDetail(
                status=ServerStatus.DISCONNECTED,
                error="连接超时"
            )
        except Exception as e:
            return "http", ConnectionDetail(
                status=ServerStatus.DISCONNECTED,
                error=str(e)
            )

    def _build_version_probe_cmd(self) -> str:
        if settings.VLLM_PYTHON_PATH:
            return (
                "echo ok && "
                f"{shlex.quote(settings.VLLM_PYTHON_PATH)} -c \"import vllm; print(vllm.__version__)\" "
                "2>/dev/null || echo unknown"
            )
        return (
            "echo ok && "
            "VLLM_PY=$(which python3 2>/dev/null || which python 2>/dev/null) && "
            "$VLLM_PY -c \"import vllm; print(vllm.__version__)\" 2>/dev/null || echo unknown"
        )

    async def _check_ssh(self) -> tuple[str, ConnectionDetail]:
        try:
            from ..executor.remote_executor import get_remote_executor
            executor = get_remote_executor()

            with self._lock:
                cached_version = self._ssh_version
            # "unknown" means the probe ran but vLLM's python was absent — do
            # not cache that; vLLM may be installed later, so re-probe.
            cmd = ("echo ok"
                   if (cached_version is not None and cached_version != "unknown")
                   else self._build_version_probe_cmd())

            ssh_start = time.time()
            result = await asyncio.to_thread(executor.execute, cmd)
            ssh_latency_ms = round((time.time() - ssh_start) * 1000, 2)

            if result["success"]:
                version = cached_version
                if version is None or version == "unknown":
                    lines = result["stdout"].strip().split('\n')
                    version = "unknown"
                    if len(lines) >= 2:
                        version = lines[1].strip() or "unknown"
                    if version != "unknown":
                        with self._lock:
                            self._ssh_version = version

                return "ssh", ConnectionDetail(
                    status=ServerStatus.CONNECTED,
                    latency_ms=ssh_latency_ms,
                    version=version
                )
            else:
                self.invalidate_ssh_version()
                return "ssh", ConnectionDetail(
                    status=ServerStatus.DISCONNECTED,
                    error=result['stderr'] or "Connection failed"
                )
        except Exception as e:
            self.invalidate_ssh_version()
            return "ssh", ConnectionDetail(
                status=ServerStatus.DISCONNECTED,
                error=str(e)
            )

    async def get_health(self) -> dict[str, Any]:
        if not self.config.host or self.config.host in ("localhost", "127.0.0.1"):
            return {"status": "no server configured"}
        target = _rc.resolve_api_target(self.config)
        def _tcp_probe() -> bool:
            try:
                with socket.create_connection(
                        (self.config.host, target.port),
                        timeout=HEALTH_REACH_PROBE_TIMEOUT):
                    return True
            except OSError:
                return False

        if not await asyncio.to_thread(_tcp_probe):
            raise httpx.ConnectError(
                f"host unreachable (TCP probe no response): "
                f"{self.config.host}:{target.port}")
        client = _rc.get_http_client()
        response = await client.get(
            f"{target.base_url}/health",
            timeout=httpx.Timeout(5.0, connect=3.0),
            headers=target.headers,
        )
        response.raise_for_status()
        try:
            return response.json()
        except Exception:
            return {"status": "healthy"}
