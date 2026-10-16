"""Environment-deployment API (facade router).

All mutation endpoints create async tasks (see ``service/deployment_*``);
read endpoints run short SSH probes. ``DeploymentError`` from the service
layer maps to HTTP 400.

Split per the file-size gate (<=300 lines): route groups live in
``deployment_routes_env.py`` (preflight / driver / CUDA / vLLM) and
``deployment_routes_tasks.py`` (task center / state / rollback), with shared
helpers in ``deployment_common.py``. This module assembles the parent router
(prefix unchanged) and re-exports the helper surface — tests patch
``api.deployment.get_remote_executor`` / ``config_manager`` and read those
through the facade in the route modules.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..config.remote_client import config_manager as config_manager
from ..executor.remote_executor import get_remote_executor as get_remote_executor
from .deployment_common import (
    _CUDA_VERSIONS as _CUDA_VERSIONS,
)
from .deployment_common import (
    _disk_free_gb as _disk_free_gb,
)
from .deployment_common import (
    _driver_sort_key as _driver_sort_key,
)
from .deployment_common import (
    _error_to_http as _error_to_http,
)
from .deployment_common import (
    _probe_in_thread as _probe_in_thread,
)
from .deployment_common import (
    _require_remote as _require_remote,
)
from .deployment_routes_env import router as _env_router
from .deployment_routes_tasks import router as _tasks_router

router = APIRouter(prefix="/api/v1/deployment", tags=["deployment"])
router.include_router(_env_router)
router.include_router(_tasks_router)
