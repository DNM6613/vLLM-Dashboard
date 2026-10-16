"""Server config manager + shared HTTP client (facade).

Split per the file-size gate (<=300 lines); this module is the
compatibility facade — it holds ``ConfigManager`` (load / save / merge with
encrypted credentials), the shared ``httpx`` client, the ``config_manager``
singleton, and re-exports the split modules so existing imports keep working:

* ``credentials`` — Fernet credential encryption / legacy migration
* ``api_target`` — resolved vLLM API connection target
* ``connection_probes`` — HTTP / SSH / TCP reachability probes (mixin)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from typing import Any

import httpx
from pydantic import ValidationError

from .api_target import (
    VLLM_DEFAULT_PORT as VLLM_DEFAULT_PORT,
)
from .api_target import (
    ApiTarget as ApiTarget,
)
from .api_target import (
    resolve_api_target as resolve_api_target,
)
from .connection_probes import ConnectionProbeMixin
from .credentials import (
    _CRED_FIELDS as _CRED_FIELDS,
)
from .credentials import (
    _encrypt_credential as _encrypt_credential,
)
from .credentials import (
    _fernet as _fernet,
)
from .credentials import (
    _get_legacy_secret_key_file as _get_legacy_secret_key_file,
)
from .credentials import (
    _migrate_legacy_encrypted as _migrate_legacy_encrypted,
)
from .model_launch_config import resolve_launch_api  # noqa: F401  (facade re-export)
from .server_config import (
    ServerConfig,
)
from .settings import settings

logger = logging.getLogger(__name__)

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


class ConfigManager(ConnectionProbeMixin):
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
            for _pw_field in _CRED_FIELDS:
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
        try:
            fernet = _fernet()
            for field in _CRED_FIELDS:
                value = config_dict.get(field)
                if isinstance(value, str) and value:
                    config_dict[field] = _encrypt_credential(fernet, value)
        except Exception as e:
            # Never fall back to plaintext credentials on disk — abort the
            # save so the previous on-disk config (already encrypted) stays.
            logger.error(
                "Config credential encryption failed; save aborted: %s", e
            )
            raise RuntimeError(
                "无法加密凭据，配置保存已中止（磁盘保留旧配置）；"
                "请检查 data/.secret_key 后重试"
            ) from e
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
            previous = self.config
            self.config = config
            try:
                self._save_config()
            except Exception:
                # Keep memory and disk in agreement when the save fails.
                self.config = previous
                raise
            self._ssh_version = None
            return self.config

    def merge_config(self, update_data: dict[str, Any]) -> ServerConfig:
        with self._lock:
            previous = self.config
            candidate = self.config.model_copy()
            for key, value in update_data.items():
                setattr(candidate, key, value)
            self.config = candidate
            try:
                self._save_config()
            except Exception:
                # Keep memory and disk in agreement when the save fails.
                self.config = previous
                raise
            self._ssh_version = None
            return self.config


config_manager = ConfigManager()
