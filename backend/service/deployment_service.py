"""Environment-deployment service (facade).

Owns:

* ``DeploymentState`` — persistent selection / snapshot / history records
  (``data/deployment_state.json``).
* ``DeploymentTaskManager`` — async mutation tasks (driver / CUDA / vLLM /
  reboot / rollback / conflict cleanup) run on daemon threads, with per-task
  log files and persisted records (stale running tasks are marked
  ``interrupted`` on startup, live remote jobs are re-attached).
* The mutation pipelines themselves (driver / CUDA / vLLM / rollback), driven
  step by step through ``TaskContext``.

Split per the file-size gate (<=300 lines); this module is the compatibility
facade — it holds the shared path constants and the two singletons, and
re-exports everything from the split modules so existing imports
(``from ..service import deployment_service as ds``) keep working unchanged:

* ``deployment_common`` — errors, compat tables, validation helpers
* ``deployment_state`` — DeploymentState persistence
* ``deployment_task_ctx`` — Task + TaskContext (steps, sudo, long steps)
* ``deployment_task_manager`` — task lifecycle + logs
* ``deployment_reattach`` — restart reattach / remote job control
* ``deployment_pipelines_env`` — driver / CUDA pipelines
* ``deployment_pipelines_vllm`` — vLLM install / rollback / reboot
* ``deployment_entry`` — public start_* entry points
"""

from __future__ import annotations

import os

from ..config.remote_client import config_manager
from ..executor.remote_executor import get_remote_executor

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
STATE_FILE = os.path.join(DATA_DIR, "deployment_state.json")
TASKS_FILE = os.path.join(DATA_DIR, "deployment_tasks.json")
LOG_DIR = os.path.join(DATA_DIR, "deployment_logs")

from .deployment_common import (
    CUDA_MIN_DRIVER,
    CUDA_VER_RE,
    DRIVER_MIN_CUDA,
    DRIVER_PKG_RE,
    PY_VER_RE,
    TASK_STATUSES,
    TERMINAL_STATUSES,
    URL_RE,
    VENV_NAME_RE,
    VLLM_MIN_DRIVER,
    VLLM_VER_RE,
    DeploymentError,
    TaskCancelled,
    _major_driver_version,
    _now_iso,
    _resolve_venv_path,
    _suggest_error,
    _validate_venv_name,
)
from .deployment_entry import (
    _require_remote,
    reconcile_driver_task,
    start_cuda_task,
    start_driver_task,
    start_reboot_task,
    start_rollback_driver,
    start_rollback_vllm,
    start_vllm_task,
)
from .deployment_pipelines_env import (
    _cuda_ver_key,
    _driver_pkg_major,
    _run_cuda,
    _run_driver,
    check_cuda_compat,
    driver_max_cuda,
)
from .deployment_pipelines_vllm import (
    _build_vllm_install_commands,
    _check_runtime_toolkit_compat,
    _run_reboot,
    _run_rollback_vllm,
    _run_vllm,
)
from .deployment_state import (
    DeploymentState,
    _default_state,
    _merge_default,
)
from .deployment_task_ctx import Task, TaskContext
from .deployment_task_manager import DeploymentTaskManager

__all__ = [
    "CUDA_MIN_DRIVER",
    "CUDA_VER_RE",
    "DRIVER_MIN_CUDA",
    "DRIVER_PKG_RE",
    "PY_VER_RE",
    "TASK_STATUSES",
    "TERMINAL_STATUSES",
    "URL_RE",
    "VENV_NAME_RE",
    "VLLM_MIN_DRIVER",
    "VLLM_VER_RE",
    "DeploymentError",
    "DeploymentState",
    "DeploymentTaskManager",
    "Task",
    "TaskCancelled",
    "TaskContext",
    "_build_vllm_install_commands",
    "_check_runtime_toolkit_compat",
    "_cuda_ver_key",
    "_default_state",
    "_driver_pkg_major",
    "_major_driver_version",
    "_merge_default",
    "_now_iso",
    "_require_remote",
    "_resolve_venv_path",
    "_run_cuda",
    "_run_driver",
    "_run_reboot",
    "_run_rollback_vllm",
    "_run_vllm",
    "_suggest_error",
    "_validate_venv_name",
    "check_cuda_compat",
    "config_manager",
    "driver_max_cuda",
    "get_remote_executor",
    "reconcile_driver_task",
    "start_cuda_task",
    "start_driver_task",
    "start_reboot_task",
    "start_rollback_driver",
    "start_rollback_vllm",
    "start_vllm_task",
]

# Module singletons (deployment_state first: the task manager's history
# recording reads it).
deployment_state = DeploymentState()
task_manager = DeploymentTaskManager()
