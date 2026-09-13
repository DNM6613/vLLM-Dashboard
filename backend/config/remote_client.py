import asyncio
import contextlib
import json
import logging
import os
import shlex
import socket
import threading
import time
from typing import Any

import httpx
from pydantic import ValidationError

from .server_config import (
    ConnectionDetail,
    DetailedConnectionStatus,
    ServerConfig,
    ServerStatus,
)
from .settings import settings

logger = logging.getLogger(__name__)

HEALTH_REACH_PROBE_TIMEOUT = 2

_shared_http_client: httpx.AsyncClient | None = None
_shared_http_client_lock = threading.Lock()

def get_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    client = _shared_http_client
    if client is None or client.is_closed:
        with _shared_http_client_lock:
            client = _shared_http_client
            if client is None or client.is_closed:
                client = httpx.AsyncClient(
                    timeout=settings.API_TIMEOUT,
                    trust_env=False
                )
                _shared_http_client = client
    return client

async def close_http_client() -> None:
    global _shared_http_client
    with _shared_http_client_lock:
        client, _shared_http_client = _shared_http_client, None
    if client and not client.is_closed:
        await client.aclose()

def get_auth_headers(config: ServerConfig | None = None) -> dict[str, str]:
    if config is None:
        config = config_manager.get_config()
    headers: dict[str, str] = {}
    if config.use_auth and config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    if config.extra_headers:
        headers.update(config.extra_headers)
    return headers

def _get_legacy_secret_key_file() -> str:
    current_file = os.path.abspath(__file__)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
    return os.path.join(project_root, "data", ".secret_key")

def _migrate_legacy_encrypted(stored: str) -> str:
    if not stored or not stored.startswith("enc:"):
        return stored
    key_file = _get_legacy_secret_key_file()
    if not os.path.exists(key_file):
        logger.warning(
            "配置含旧版加密密码但密钥文件缺失（%s），密码已清空，请重新设置", key_file
        )
        return ""
    try:
        from cryptography.fernet import Fernet
        with open(key_file, "rb") as f:
            fernet = Fernet(f.read())
        return fernet.decrypt(stored[4:].encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.warning(
            "旧版加密密码解密失败（密码已清空，请重新设置）: %s", e
        )
        return ""

class ConfigManager:
    def __init__(self):
        current_file = os.path.abspath(__file__)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
        self.config_file = os.path.join(project_root, "data", "server_config.json")
        self._lock = threading.RLock()
        self.config: ServerConfig = self._load_config()
        self._ssh_version: str | None = None

    def invalidate_ssh_version(self) -> None:
        with self._lock:
            self._ssh_version = None

    def _load_config(self) -> ServerConfig:
        data: dict[str, Any] | None = None
        if os.path.exists(self.config_file):
            if os.name == "posix":
                with contextlib.suppress(OSError):
                    os.chmod(self.config_file, 0o600)
            try:
                with open(self.config_file, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                self._quarantine_corrupt(e)
                return ServerConfig()
            if not isinstance(data, dict):
                self._quarantine_corrupt(f"顶层不是对象: {type(data).__name__}")
                return ServerConfig()

        if data is not None:
            for _pw_field in ("ssh_password", "bmc_password"):
                _pw = data.get(_pw_field, "")
                if isinstance(_pw, str) and _pw.startswith("enc:"):
                    data[_pw_field] = _migrate_legacy_encrypted(_pw)

        if data is None:
            return ServerConfig()
        try:
            return ServerConfig(**data)
        except ValidationError as e:
            if all(err["loc"] and err["loc"][0] == "host" for err in e.errors()):
                _host = data.get("host")
                _data = dict(data)
                _data.pop("host", None)
                logger.warning(
                    "Server config host %r rejected by IPv4 validation "
                    "(v2.8.3+); loaded without host — other fields preserved, "
                    "set an IPv4 address in Server Config", _host)
                return ServerConfig(**_data)
            self._quarantine_corrupt(e, kind="invalid")
            return ServerConfig()
        except (KeyError, TypeError, ValueError) as e:
            self._quarantine_corrupt(e)
            return ServerConfig()

    def _quarantine_corrupt(self, reason: Any, *, kind: str = "corrupted") -> None:
        if kind == "invalid":
            logger.error(
                "Server config failed field validation (JSON well-formed), "
                "backing up and using defaults: %s", reason)
        else:
            logger.error(
                "Server config corrupted, backing up and using defaults: %s",
                reason)
        try:
            corrupt_backup = self.config_file + ".corrupt"
            os.replace(self.config_file, corrupt_backup)
            logger.error("Corrupted config moved to %s", corrupt_backup)
        except OSError:
            pass

    def _save_config(self):
        config_dir = os.path.dirname(self.config_file)
        os.makedirs(config_dir, exist_ok=True)
        config_dict = self.config.model_dump()
        tmp_file = self.config_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
        if os.name == "posix":
            os.chmod(tmp_file, 0o600)
        os.replace(tmp_file, self.config_file)

    def get_config(self) -> ServerConfig:
        with self._lock:
            return self.config

    def update_config(self, config: ServerConfig) -> ServerConfig:
        with self._lock:
            self.config = config
            self._save_config()
            self._ssh_version = None
            return self.config

    def merge_config(self, update_data: dict[str, Any]) -> ServerConfig:
        with self._lock:
            candidate = self.config.model_copy()
            for key, value in update_data.items():
                setattr(candidate, key, value)
            self.config = candidate
            self._save_config()
            self._ssh_version = None
            return self.config

    async def check_connection(self, refresh: bool = False) -> DetailedConnectionStatus:
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

        return DetailedConnectionStatus(
            http=http_status,
            ssh=ssh_status,
            overall=overall
        )

    async def _check_http(self) -> tuple[str, ConnectionDetail]:
        start = time.time()
        try:
            client = get_http_client()
            response = await client.get(
                f"{self.config.get_base_url()}/v1/models",
                timeout=3.0,
                headers=get_auth_headers(self.config),
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
            cmd = "echo ok" if cached_version is not None else self._build_version_probe_cmd()

            ssh_start = time.time()
            result = await asyncio.to_thread(executor.execute, cmd)
            ssh_latency_ms = round((time.time() - ssh_start) * 1000, 2)

            if result["success"]:
                version = cached_version
                if version is None:
                    lines = result["stdout"].strip().split('\n')
                    version = "unknown"
                    if len(lines) >= 2:
                        version = lines[1].strip() or "unknown"
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
        def _tcp_probe() -> bool:
            try:
                with socket.create_connection(
                        (self.config.host, self.config.port),
                        timeout=HEALTH_REACH_PROBE_TIMEOUT):
                    return True
            except OSError:
                return False

        if not await asyncio.to_thread(_tcp_probe):
            raise httpx.ConnectError(
                f"host unreachable (TCP probe no response): "
                f"{self.config.host}:{self.config.port}")
        client = get_http_client()
        response = await client.get(
            f"{self.config.get_base_url()}/health",
            timeout=httpx.Timeout(5.0, connect=3.0),
            headers=get_auth_headers(self.config),
        )
        response.raise_for_status()
        try:
            return response.json()
        except Exception:
            return {"status": "healthy"}

config_manager = ConfigManager()
