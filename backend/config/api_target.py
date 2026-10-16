"""Resolved vLLM API connection target.

Split out of ``remote_client.py`` (file-size gate). ``resolve_launch_api``
and the ``config_manager`` singleton are read through the ``remote_client``
facade at call time so tests patching ``remote_client.resolve_launch_api``
keep working.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import remote_client as _rc
from .server_config import ServerConfig

# vLLM's default API port, used when neither the launch command (--port) nor
# the environment (PORT) specifies one.
VLLM_DEFAULT_PORT = 8000


@dataclass
class ApiTarget:
    """A resolved vLLM API connection target.

    The port and API key come from the launch config of the model currently
    being served (``--port`` / ``--api-key`` flags or ``PORT`` / ``API_KEY``
    env vars); when no port is specified there, the vLLM default port is
    used. ``api_key`` is the effective key (the one actually sent in the
    Authorization header, or ``None`` when the API is unauthenticated).
    """
    base_url: str
    port: int
    headers: dict
    api_key: str | None


def resolve_api_target(config: ServerConfig | None = None) -> ApiTarget:
    if config is None:
        config = _rc.config_manager.get_config()
    port, parsed_key = _rc.resolve_launch_api()
    if port is None:
        port = VLLM_DEFAULT_PORT
    api_key = parsed_key
    if api_key is None and config.use_auth and config.api_key:
        api_key = config.api_key
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if config.extra_headers:
        headers.update(config.extra_headers)
    return ApiTarget(
        base_url=f"http://{config.host}:{port}",
        port=port,
        headers=headers,
        api_key=api_key,
    )
