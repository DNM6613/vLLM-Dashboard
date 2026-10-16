"""Model service (facade).

Split per the file-size gate (<=300 lines); this module is the
compatibility facade — it holds the shared mutable state (benchmark result
store, download caches, in-flight guards, start single-flight lock) and the
patchable dependency hooks, and re-exports everything from the split modules
so existing imports (``from ..service import model_service``) and tests that
patch ``model_service.X`` keep working unchanged:

* ``model_status_sync`` — status reconciliation with the remote vLLM API
* ``model_benchmark`` — benchmark runs, result store, auto-benchmark
* ``model_downloads`` — download status / download / CLI tool install
* ``model_lifecycle`` — start / stop (remote stop verification, env unset)
* ``model_catalog`` — scan / get / delete
"""

from __future__ import annotations

import asyncio
import logging
import threading

from ..config.model_launch_config import launch_config_manager, parse_env_vars
from ..config.remote_client import config_manager, get_http_client, resolve_api_target
from ..config.settings import settings
from ..console.session_manager import console_session_manager
from ..controller.state_machine import state_machine
from ..executor.remote.model_ops import DL_PROBE_SENTINEL, DL_PROCESS_PATTERN
from ..executor.remote.ssh_probe import check_ssh_connectivity as check_ssh_connectivity
from ..executor.remote_executor import get_remote_executor
from ..schemas.model import BenchmarkInfo, ModelInfo, ModelStatus

# Shared constants must be defined before the submodule imports below:
# model_status_sync binds LOADING_* as function-signature defaults.
LOADING_GRACE_SECONDS = 120
LOADING_HARD_CAP_SECONDS = 1800
_PROBE_TCP_TIMEOUT = 3.0

# A running download whose log has not grown for this long is treated as
# stalled (the process is alive but the log is no longer advancing — typically
# a hung network read that `hf` is not recovering from).
DL_STALL_THRESHOLD_SECS = 180
# Repo -> (fetched_at, total_bytes); the HF API total is stable, so a long
# TTL keeps status polling (every ~3s) from hammering the API.
DL_TOTAL_TTL_SECS = 3600

_START_PROBE_DELAY = 10.0

# Single-flight start: the status check and the LOADING transition must be
# atomic, or two concurrent calls both pass the guard and both deliver.
_start_lock = asyncio.Lock()
_start_probe_tasks: set[asyncio.Task] = set()

_benchmark_inflight: set[str] = set()
_benchmark_inflight_lock = threading.Lock()
_benchmark_store_lock = threading.Lock()
_auto_bench_tasks: set[asyncio.Task] = set()

# log_file -> (pid, last_size_bytes, ts of last growth). Byte growth is the
# stall signal: the `hf` CLI only appends one line per *file* to a redirected
# log, so the log mtime is silent for minutes while a large file transfers.
# The pid resets the baseline so a re-download of the same repo (new process,
# smaller size) is not mistaken for a stall.
_dl_growth: dict[str, tuple[str, int, float]] = {}
_dl_total_cache: dict[str, tuple[float, int]] = {}

from .model_benchmark import (
    _AUTO_BENCH_PENDING_FILE as _AUTO_BENCH_PENDING_FILE,
)
from .model_benchmark import (
    _BENCHMARK_RESULTS_FILE as _BENCHMARK_RESULTS_FILE,
)
from .model_benchmark import (
    BENCHMARK_MAX_TOKENS as BENCHMARK_MAX_TOKENS,
)
from .model_benchmark import (
    BENCHMARK_PROMPT as BENCHMARK_PROMPT,
)
from .model_benchmark import (
    _auto_benchmark as _auto_benchmark,
)
from .model_benchmark import (
    _benchmark_acquire as _benchmark_acquire,
)
from .model_benchmark import (
    _benchmark_precheck as _benchmark_precheck,
)
from .model_benchmark import (
    _benchmark_release as _benchmark_release,
)
from .model_benchmark import (
    _get_benchmark_result as _get_benchmark_result,
)
from .model_benchmark import (
    _load_persisted_json as _load_persisted_json,
)
from .model_benchmark import (
    _mark_auto_bench_pending as _mark_auto_bench_pending,
)
from .model_benchmark import (
    _maybe_trigger_auto_bench as _maybe_trigger_auto_bench,
)
from .model_benchmark import (
    _persist_persisted as _persist_persisted,
)
from .model_benchmark import (
    _record_benchmark_result as _record_benchmark_result,
)
from .model_benchmark import (
    _remove_benchmark_result as _remove_benchmark_result,
)
from .model_benchmark import (
    _take_auto_bench_pending as _take_auto_bench_pending,
)
from .model_benchmark import (
    _with_benchmark as _with_benchmark,
)
from .model_benchmark import (
    _write_persisted_json as _write_persisted_json,
)
from .model_benchmark import (
    benchmark_model as benchmark_model,
)
from .model_catalog import (
    _model_delete_target as _model_delete_target,
)
from .model_catalog import (
    delete_model as delete_model,
)
from .model_catalog import (
    get_model as get_model,
)
from .model_catalog import (
    scan_models as scan_models,
)
from .model_downloads import (
    _build_download_status_command as _build_download_status_command,
)
from .model_downloads import (
    _get_download_total as _get_download_total,
)
from .model_downloads import (
    check_cli_tools as check_cli_tools,
)
from .model_downloads import (
    download_model as download_model,
)
from .model_downloads import (
    ensure_hf_mirror as ensure_hf_mirror,
)
from .model_downloads import (
    get_download_status as get_download_status,
)
from .model_downloads import (
    get_install_status as get_install_status,
)
from .model_downloads import (
    install_cli_tool as install_cli_tool,
)
from .model_downloads import (
    stop_download as stop_download,
)
from .model_lifecycle import (
    _build_stop_wait_command as _build_stop_wait_command,
)
from .model_lifecycle import (
    _start_model as _start_model,
)
from .model_lifecycle import (
    _unset_launch_env_vars as _unset_launch_env_vars,
)
from .model_lifecycle import (
    _verify_start_probe as _verify_start_probe,
)
from .model_lifecycle import (
    _wait_for_remote_stop as _wait_for_remote_stop,
)
from .model_lifecycle import (
    start_model as start_model,
)
from .model_lifecycle import (
    stop_model as stop_model,
)
from .model_status_sync import (
    _check_vllm_present_strict as _check_vllm_present_strict,
)
from .model_status_sync import (
    _loading_hard_reset_allowed as _loading_hard_reset_allowed,
)
from .model_status_sync import (
    _loading_reset_allowed as _loading_reset_allowed,
)
from .model_status_sync import (
    _match_model_name as _match_model_name,
)
from .model_status_sync import (
    _match_served_id as _match_served_id,
)
from .model_status_sync import (
    _sync_status_once as _sync_status_once,
)
from .model_status_sync import (
    list_models as list_models,
)
from .model_status_sync import (
    sync_status as sync_status,
)
from .model_watchdog import (
    apply_model_lock as apply_model_lock,
)
from .model_watchdog import (
    on_model_crash as _watchdog_on_crash,
)
from .model_watchdog import (
    start_watchdog as start_watchdog,
)
from .model_watchdog import (
    stop_watchdog as stop_watchdog,
)

logger = logging.getLogger(__name__)

_benchmark_results: dict[str, dict] = _load_persisted_json(_BENCHMARK_RESULTS_FILE, {})
_auto_bench_pending: set[str] = set(_load_persisted_json(_AUTO_BENCH_PENDING_FILE, []))

__all__ = [
    "BENCHMARK_MAX_TOKENS",
    "BENCHMARK_PROMPT",
    "DL_PROBE_SENTINEL",
    "DL_PROCESS_PATTERN",
    "DL_STALL_THRESHOLD_SECS",
    "DL_TOTAL_TTL_SECS",
    "LOADING_GRACE_SECONDS",
    "LOADING_HARD_CAP_SECONDS",
    "BenchmarkInfo",
    "ModelInfo",
    "ModelStatus",
    "_watchdog_on_crash",
    "apply_model_lock",
    "benchmark_model",
    "check_cli_tools",
    "config_manager",
    "console_session_manager",
    "delete_model",
    "download_model",
    "ensure_hf_mirror",
    "get_download_status",
    "get_http_client",
    "get_install_status",
    "get_model",
    "get_remote_executor",
    "install_cli_tool",
    "launch_config_manager",
    "list_models",
    "parse_env_vars",
    "resolve_api_target",
    "scan_models",
    "settings",
    "start_model",
    "start_watchdog",
    "state_machine",
    "stop_download",
    "stop_model",
    "stop_watchdog",
    "sync_status",
]
