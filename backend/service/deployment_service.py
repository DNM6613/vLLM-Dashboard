"""Environment-deployment service.

Owns three things:

* ``DeploymentState`` — persistent selection / snapshot / history records
  (``data/deployment_state.json``), same JSON + atomic-write pattern as
  ``ConfigManager``.
* ``DeploymentTaskManager`` — async mutation tasks (driver / CUDA / vLLM /
  reboot / rollback / conflict cleanup) run on daemon threads, with
  per-task log files under ``data/deployment_logs/`` and persisted records
  in ``data/deployment_tasks.json`` (stale running tasks are marked
  ``interrupted`` on startup).
* The mutation pipelines themselves. Long apt steps run as remote
  ``nohup bash`` jobs (same pattern as model downloads) so their output
  streams into the task log while the step is in flight; the sudo password
  is delivered over SSH stdin and written to a 0600 ``/tmp`` file that the
  wrapper ``sudo()`` function reads — it never appears on a command line.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import datetime
from typing import Any

from ..config.remote_client import config_manager
from ..config.server_config import has_parent_path_segment
from ..executor.remote.deployment_ops import CUDA_VERSION_PROBE, UV_PATH_PREFIX
from ..executor.remote_executor import get_remote_executor

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
STATE_FILE = os.path.join(DATA_DIR, "deployment_state.json")
TASKS_FILE = os.path.join(DATA_DIR, "deployment_tasks.json")
LOG_DIR = os.path.join(DATA_DIR, "deployment_logs")

# ---- compatibility rules (spec) -------------------------------------------
# Minimum driver major per CUDA version — NVIDIA CUDA Toolkit release notes
# (Linux x86_64): CUDA 12.9 >= 575.57.08, CUDA 13.0 GA >= 580.65.06.
CUDA_MIN_DRIVER: dict[str, int] = {"12.9": 575, "13.0": 580}
# vLLM recommended minimum driver.
VLLM_MIN_DRIVER = 550
# Drivers whose release line requires a newer CUDA floor (driver-tab warning).
DRIVER_MIN_CUDA: dict[int, str] = {610: "12.8"}

# ---- input validation ------------------------------------------------------
DRIVER_PKG_RE = re.compile(r"^nvidia-driver-\d{3,4}(-[a-z0-9]+)*$")
CUDA_VER_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")
VLLM_VER_RE = re.compile(r"^\d+\.\d+\.\d+$")
VENV_NAME_RE = re.compile(r"^[\w./\-~]{1,128}$")
PY_VER_RE = re.compile(r"^\d+\.\d{1,2}$")
PKG_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.+\-]*$")
URL_RE = re.compile(r"^https?://[\w.\-]+(?::\d+)?(/\S*)?$")

TASK_STATUSES = ("queued", "running", "success", "failed",
                 "cancelled", "awaiting_reboot", "interrupted")
TERMINAL_STATUSES = ("success", "failed", "cancelled", "interrupted")


class DeploymentError(Exception):
    """Fatal pipeline failure with a user-facing message."""


class TaskCancelled(Exception):
    """Raised between steps when the user cancels a task."""


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _major_driver_version(version: str) -> int | None:
    m = re.match(r"^(\d{3,4})\.", (version or "").strip())
    return int(m.group(1)) if m else None


def _validate_venv_name(venv_name: str) -> None:
    if not VENV_NAME_RE.fullmatch(venv_name or "") or has_parent_path_segment(venv_name):
        raise DeploymentError(f"Invalid venv name: {venv_name}")


def _resolve_venv_path(venv_name: str) -> str:
    """Remote shell path for the venv (same semantics as RemoteExecutor
    ``_get_activate_cmd``): bare name under $HOME, ``~/...`` and ``/abs``."""
    if venv_name.startswith("/"):
        return venv_name
    if venv_name == "~":
        return "$HOME"
    if venv_name.startswith("~/"):
        return f"$HOME/{venv_name[2:]}"
    return f"$HOME/{venv_name}"


def _suggest_error(message: str) -> str:
    m = message.lower()
    if "timed out" in m or "timeout" in m:
        return "Network or package repository is slow. Try switching the mirror source (Mirror Sources panel) and retry."
    if "unable to locate package" in m or "no candidate" in m:
        return "Package not found in the configured repositories. Check the selected version."
    if "could not get lock" in m or "dpkg lock" in m:
        return "Another apt/dpkg process is running. Wait for it to finish, then retry."
    if "no space left" in m:
        return "Insufficient disk space. Free up space and retry."
    if "password" in m or "authentication" in m or "sudo" in m:
        return "sudo authentication failed. The SSH password must match the sudo password."
    if "could not resolve" in m or "unreachable" in m or "curl" in m:
        return "Network unreachable from the target server. Check its egress / mirror configuration."
    return ""


# ===========================================================================
# Persistent state
# ===========================================================================

def _default_state() -> dict[str, Any]:
    return {
        "mirrors": {"pypi": "", "hf": "https://hf-mirror.com"},
        "selected": {
            "driver": "",
            "cuda": "",
            # System CUDA Toolkit is the robust default: it provides nvcc for
            # JIT/AOT kernel compilation that some models require at startup.
            # The vLLM built-in runtime alone covers most cases but can fail
            # to start models that need the compiler.
            "cuda_install_system": True,
            "vllm_version": "",
            "vllm_runtime": "builtin",
        },
        "snapshots": {
            "driver": {"current_pkg": "", "previous_pkg": "", "previous_version": ""},
            "cuda": {"installed_version": "", "toolkit_path": ""},
            "vllm": {"version": "", "previous_version": "", "venv": ""},
        },
        "history": [],
    }


def _merge_default(default: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``data`` over ``default``; unknown keys are dropped so the
    schema stays strict as it grows."""
    out: dict[str, Any] = {}
    for key, dval in default.items():
        if key not in data:
            out[key] = dval
        elif isinstance(dval, dict) and isinstance(data[key], dict):
            out[key] = _merge_default(dval, data[key])
        else:
            out[key] = data[key]
    return out


class DeploymentState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        default = _default_state()
        if not os.path.exists(STATE_FILE):
            return default
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("top-level is not an object")
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning("deployment state unreadable (%s); using defaults", e)
            with suppress(OSError):
                os.replace(STATE_FILE, STATE_FILE + ".corrupt")
            return default
        merged = _merge_default(default, data)
        if len(merged["history"]) > 100:
            merged["history"] = merged["history"][-100:]
        return merged

    def _save(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        if os.name == "posix":
            with suppress(OSError):
                os.chmod(tmp, 0o600)
        os.replace(tmp, STATE_FILE)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.data))

    def update_section(self, section: str, **values: Any) -> None:
        with self._lock:
            target = self.data.get(section)
            if isinstance(target, dict):
                for key, value in values.items():
                    if key in target:
                        target[key] = value
            self._save()

    def set_snapshot(self, kind: str, **values: Any) -> None:
        with self._lock:
            target = self.data["snapshots"].get(kind)
            if isinstance(target, dict):
                for key, value in values.items():
                    if key in target:
                        target[key] = value
            self._save()

    def push_history(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.data["history"].append(entry)
            self.data["history"] = self.data["history"][-100:]
            self._save()


# ===========================================================================
# Task manager
# ===========================================================================

class Task:
    def __init__(self, task_id: str, kind: str, title: str, steps: list[str]) -> None:
        self.id = task_id
        self.kind = kind
        self.title = title
        self.steps = [{"name": s, "status": "pending"} for s in steps]
        self.current_step = -1
        self.status = "queued"
        self.error = ""
        self.suggestion = ""
        self.result: dict[str, Any] = {}
        self.created_at = _now_iso()
        self.started_at = ""
        self.finished_at = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "steps": [dict(s) for s in self.steps],
            "current_step": self.current_step,
            "error": self.error,
            "suggestion": self.suggestion,
            "result": dict(self.result),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        task = cls(data["id"], data["kind"], data["title"],
                   [s["name"] for s in data.get("steps", [])])
        for step, stored in zip(task.steps, data.get("steps", [])):
            step["status"] = stored.get("status", "pending")
        task.current_step = data.get("current_step", -1)
        task.status = data.get("status", "queued")
        task.error = data.get("error", "")
        task.suggestion = data.get("suggestion", "")
        task.result = dict(data.get("result", {}))
        task.created_at = data.get("created_at", _now_iso())
        task.started_at = data.get("started_at", "")
        task.finished_at = data.get("finished_at", "")
        return task


class DeploymentTaskManager:
    MAX_TASKS = 50
    TAIL_LINES = 400
    LOG_CHUNK = 256 * 1024

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._tails: dict[str, deque] = {}
        self._recover()
        with self._lock:
            self._persist_locked()

    # ---- persistence ------------------------------------------------------
    def _recover(self) -> None:
        if not os.path.exists(TASKS_FILE):
            return
        try:
            with open(TASKS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("deployment tasks file unreadable (%s); starting fresh", e)
            return
        if not isinstance(data, list):
            return
        for entry in reversed(data[-self.MAX_TASKS:]):
            try:
                task = Task.from_dict(entry)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("dropping malformed task record: %s", e)
                continue
            if task.status in ("queued", "running"):
                task.status = "interrupted"
                task.error = task.error or "Interrupted by dashboard restart"
                task.finished_at = task.finished_at or _now_iso()
            self._tasks[task.id] = task

    def _persist_locked(self) -> None:
        entries = [t.to_dict() for t in list(self._tasks.values())[-self.MAX_TASKS:]]
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = TASKS_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)
            os.replace(tmp, TASKS_FILE)
        except OSError as e:
            logger.warning("failed to persist deployment tasks: %s", e)

    # ---- lifecycle ---------------------------------------------------------
    def create(self, kind: str, title: str, steps: list[str]) -> Task:
        task_id = f"dep_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
        task = Task(task_id, kind, title, steps)
        with self._lock:
            self._tasks[task_id] = task
            self._cancel[task_id] = threading.Event()
            self._persist_locked()
        return task

    def submit(self, task: Task, runner: Callable[[TaskContext], None]) -> None:
        def target() -> None:
            with self._lock:
                task.status = "running"
                task.started_at = _now_iso()
                self._persist_locked()
            ctx = TaskContext(task, self)
            try:
                runner(ctx)
                with self._lock:
                    if task.status == "running":
                        task.status = "success"
                        task.finished_at = _now_iso()
                        self._persist_locked()
                self._record_history(task)
            except TaskCancelled:
                with self._lock:
                    task.status = "cancelled"
                    task.finished_at = _now_iso()
                    for step in task.steps:
                        if step["status"] in ("pending", "running"):
                            step["status"] = "skipped"
                    self._persist_locked()
            except DeploymentError as e:
                self._fail_locked(task, str(e))
            except Exception as e:
                self._fail_locked(task, f"{type(e).__name__}: {e}")

        thread = threading.Thread(target=target, name=f"deploy-{task.kind}", daemon=True)
        with self._lock:
            self._threads[task.id] = thread
        thread.start()

    def _fail_locked(self, task: Task, message: str) -> None:
        with self._lock:
            task.status = "failed"
            task.error = message
            task.suggestion = _suggest_error(message)
            task.finished_at = _now_iso()
            self._persist_locked()
        self._record_history(task)
        logger.warning("deployment task %s failed: %s", task.id, message)

    def _record_history(self, task: Task) -> None:
        if task.status not in ("success", "failed", "cancelled"):
            return
        deployment_state.push_history({
            "ts": task.finished_at or _now_iso(),
            "kind": task.kind,
            "title": task.title,
            "status": task.status,
        })

    # ---- queries / control --------------------------------------------------
    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, limit: int = 30) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        return list(reversed(tasks))[:limit]

    def pending_driver_task(self) -> Task | None:
        with self._lock:
            candidates = [t for t in self._tasks.values()
                          if t.kind == "driver" and t.status == "awaiting_reboot"]
        return candidates[-1] if candidates else None

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            event = self._cancel.get(task_id)
            if task is None or event is None or task.status not in ("queued", "running"):
                return False
            event.set()
            return True

    def complete_driver(self, task: Task) -> None:
        """Mark a post-reboot driver task successful (reconcile path)."""
        with self._lock:
            task.status = "success"
            task.finished_at = _now_iso()
            for step in task.steps:
                if step["status"] != "done":
                    step["status"] = "done"
            self._persist_locked()
        self._record_history(task)

    # ---- logs ----------------------------------------------------------------
    def log(self, task_id: str, message: str) -> None:
        with self._lock:
            if task_id not in self._tasks:
                return
            line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
            tail = self._tails.setdefault(task_id, deque(maxlen=self.TAIL_LINES))
            tail.append(line)
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(os.path.join(LOG_DIR, f"{task_id}.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.warning("failed to append deployment log for %s: %s", task_id, e)

    def tail(self, task_id: str, n: int = 200) -> list[str]:
        with self._lock:
            tail = self._tails.get(task_id)
            return list(tail)[-n:] if tail else []

    def read_log(self, task_id: str, offset: int = 0) -> tuple[str, int]:
        """Log content from byte ``offset`` (incremental fetch) + new offset."""
        with self._lock:
            if task_id not in self._tasks:
                return "", offset
        path = os.path.join(LOG_DIR, f"{task_id}.log")
        try:
            with open(path, "rb") as f:
                f.seek(max(0, offset))
                data = f.read(self.LOG_CHUNK)
        except OSError:
            return "", offset
        return data.decode("utf-8", "replace"), max(0, offset) + len(data)


# ===========================================================================
# Task execution context
# ===========================================================================

class TaskContext:
    """Per-task plumbing: executor, logging, steps, sudo, long-running steps."""

    def __init__(self, task: Task, manager: DeploymentTaskManager) -> None:
        self.task = task
        self.manager = manager
        self.executor = get_remote_executor()
        self._sudo_mode: bool | None = None  # True = passwordless sudo

    # -- logging / cancellation ------------------------------------------------
    def log(self, message: str) -> None:
        for line in str(message).splitlines() or [""]:
            self.manager.log(self.task.id, line)

    def check_cancelled(self) -> None:
        with self.manager._lock:
            event = self.manager._cancel.get(self.task.id)
        if event is not None and event.is_set():
            raise TaskCancelled()

    @contextmanager
    def step(self, index: int):
        self.check_cancelled()
        name = self.task.steps[index]["name"] if index < len(self.task.steps) else f"step {index}"
        with self.manager._lock:
            self.task.current_step = index
            if index < len(self.task.steps):
                self.task.steps[index]["status"] = "running"
            self.manager._persist_locked()
        self.log(f"-- {name} --")
        try:
            yield
        except TaskCancelled:
            self._mark_step(index, "cancelled")
            raise
        except Exception:
            self._mark_step(index, "failed")
            raise
        else:
            self._mark_step(index, "done")

    def _mark_step(self, index: int, status: str) -> None:
        with self.manager._lock:
            if index < len(self.task.steps):
                self.task.steps[index]["status"] = status
            self.manager._persist_locked()

    # -- command execution -------------------------------------------------------
    def exec(self, command: str, timeout: int | None = None,
             stdin_data: str | None = None) -> dict[str, Any]:
        self.log(f"$ {command}")
        result = self.executor.execute(command, timeout=timeout, stdin_data=stdin_data)
        stdout = (result.get("stdout") or "").strip()
        stderr = (result.get("stderr") or "").strip()
        if stdout:
            self.log(stdout if len(stdout) <= 4000 else stdout[-4000:])
        if stderr:
            self.log(stderr if len(stderr) <= 2000 else stderr[-2000:])
        return result

    def sudo(self, command: str, timeout: int | None = None) -> dict[str, Any]:
        """Run ``command`` through sudo, probing the sudo mode once per task."""
        if self._sudo_mode is None:
            probe = self.executor.execute("sudo -n -p '' true", timeout=10)
            self._sudo_mode = bool(probe.get("success"))
            if not self._sudo_mode and not self.executor.password:
                raise DeploymentError(
                    "sudo requires a password but no SSH password is configured "
                    "(set it in Server Config)"
                )
        if self._sudo_mode:
            return self.exec(f"sudo -n -p '' {command}", timeout=timeout)
        return self.exec(f"sudo -S -p '' {command}", timeout=timeout,
                         stdin_data=self.executor.password + "\n")

    # -- long steps (remote nohup, streamed log) --------------------------------
    def run_long(self, command: str, timeout: int = 1800) -> dict[str, Any]:
        """Run ``command`` as a remote background job and stream its output.

        The wrapper defines ``sudo()`` (passwordless or password-fed via a
        0600 temp file written from SSH stdin) and records the exit code in
        an exit file; polling reads the log incrementally every 2.5s.
        """
        self.check_cancelled()
        self.sudo("true", timeout=20)  # probe mode + refresh timestamp

        tid = self.task.id
        pw_file = f"/tmp/.vdb_pw_{tid}"
        log_file = f"/tmp/vdb_deploy_{tid}.log"
        pid_file = f"{log_file}.pid"
        exit_file = f"{log_file}.exit"
        if self._sudo_mode:
            sudo_fn = 'sudo() { command sudo -n -p "" "$@"; }'
        else:
            sudo_fn = f'sudo() {{ command sudo -S -p "" "$@" < {pw_file}; }}'
        wrapper = (
            f"{sudo_fn}; "
            f"{command}; "
            f'ec=$?; rm -f {pw_file}; echo "VDB_EXIT:$ec" > {exit_file}'
        )
        escaped = self.executor._escape_double_quoted(wrapper)
        if self._sudo_mode:
            launch = (
                f"rm -f {log_file} {pid_file} {exit_file}; "
                f'nohup bash -c "{escaped}" > {log_file} 2>&1 & '
                f"P=$!; echo $P > {pid_file}; echo $P"
            )
            stdin_data: str | None = None
        else:
            launch = (
                "read -r VDB_PW && printf '%s\\n' \"$VDB_PW\" > " + pw_file
                + f" && chmod 600 {pw_file} && "
                f"rm -f {log_file} {pid_file} {exit_file}; "
                f'nohup bash -c "{escaped}" > {log_file} 2>&1 & '
                f"P=$!; echo $P > {pid_file}; echo $P"
            )
            stdin_data = self.executor.password + "\n"
        result = self.exec(launch, timeout=30, stdin_data=stdin_data)
        if not result.get("success"):
            raise DeploymentError(f"Failed to start background step: {result.get('stderr') or 'unknown error'}")

        marker = "VDB_POLL"
        offset = 0
        deadline = time.monotonic() + timeout
        gone_polls = 0
        while True:
            self.check_cancelled()
            if time.monotonic() > deadline:
                self._cleanup_long(pw_file, log_file, pid_file, exit_file)
                raise DeploymentError(f"Step timed out after {timeout}s")
            time.sleep(2.5)
            poll = self.executor.execute(
                f"tail -c +{offset + 1} {log_file} 2>/dev/null; echo; echo {marker}; "
                f"if [ -f {exit_file} ]; then cat {exit_file}; "
                f"elif kill -0 $(cat {pid_file} 2>/dev/null) 2>/dev/null; then echo VDB_RUNNING; "
                f"else echo VDB_GONE; fi",
                timeout=30,
            )
            out = poll.get("stdout") or ""
            idx = out.rfind(f"\n{marker}\n")
            content = out[: idx + 1] if idx >= 0 else out
            status = out[idx + len(marker) + 1:].strip() if idx >= 0 else ""
            if content:
                for line in content.splitlines():
                    if line.strip():
                        self.log(line)
                offset += len(content.encode("utf-8"))

            if status.startswith("VDB_EXIT:"):
                try:
                    code = int(status.split(":", 1)[1])
                except ValueError:
                    code = -1
                self._cleanup_long(pw_file, log_file, pid_file, exit_file)
                if code != 0:
                    raise DeploymentError(f"Step failed (exit code {code})")
                return {"success": True, "exit_code": 0}
            if status == "VDB_RUNNING":
                gone_polls = 0
            elif status == "VDB_GONE":
                gone_polls += 1
                if gone_polls >= 3:
                    self._cleanup_long(pw_file, log_file, pid_file, exit_file)
                    raise DeploymentError("Background step process exited unexpectedly")
            # empty status (transient) -> just poll again

    def _cleanup_long(self, *paths: str) -> None:
        with suppress(Exception):
            self.executor.execute(f"rm -f {' '.join(paths)}", timeout=10)


# ===========================================================================
# Pipelines
# ===========================================================================

def _require_remote() -> None:
    config = config_manager.get_config()
    if not config.host or config.host in ("localhost", "127.0.0.1"):
        raise DeploymentError(
            "Environment deployment requires a remote SSH server (set the host in Server Config)"
        )


def check_cuda_compat(driver_major: int | None, cuda_version: str) -> str | None:
    required = CUDA_MIN_DRIVER.get(cuda_version)
    if required and driver_major is not None and driver_major < required:
        return (f"Current driver {driver_major} does not meet CUDA {cuda_version}'s "
                f"minimum requirement {required}; upgrade the driver first")
    return None


def _run_driver(ctx: TaskContext, package: str, target_major: int | None) -> None:
    with ctx.step(0):
        stop = ctx.executor.stop_vllm(force=False)
        if stop.get("success"):
            ctx.log(f"vLLM stopped (mode: {stop.get('mode')})")
        else:
            ctx.log(f"warning: stop_vllm: {stop.get('error')}")
    with ctx.step(1):
        ctx.run_long(
            "apt-get remove --purge -y 'nvidia-*' && apt-get autoremove -y && apt-get autoclean",
            timeout=1800,
        )
    with ctx.step(2):
        ctx.run_long(f"apt-get update -y && apt-get install -y {package}", timeout=3600)
        deployment_state.set_snapshot("driver", current_pkg=package)
    with ctx.manager._lock:
        ctx.task.status = "awaiting_reboot"
        if len(ctx.task.steps) > 3:
            ctx.task.steps[3]["status"] = "running"
        ctx.manager._persist_locked()
    ctx.log("Driver install complete. A server reboot is required for it to take effect; "
            "after the server is back this page verifies the new driver automatically.")


def _run_cuda(ctx: TaskContext, version: str, pkg: str) -> None:
    repo_script = (
        'UBU=$(lsb_release -cs 2>/dev/null || { . /etc/os-release && echo "$VERSION_CODENAME"; }); '
        '[ -n "$UBU" ] || { echo "cannot detect Ubuntu codename" >&2; exit 1; }; '
        "sudo install -d /etc/apt/keyrings; "
        "sudo curl -fsSL https://ubuntu.pkgs.nvidia.com/cuda/ubuntu-dist/$UBU/Release.key "
        "-o /etc/apt/keyrings/cuda-vdb.asc; "
        "sudo tee /etc/apt/sources.list.d/cuda-vdb.list >/dev/null <<VDBEOF\n"
        "deb [signed-by=/etc/apt/keyrings/cuda-vdb.asc] https://ubuntu.pkgs.nvidia.com/cuda/ubuntu-dist/$UBU/ /\n"
        "VDBEOF\n"
        "sudo apt-get update -y"
    )
    with ctx.step(0):
        ctx.run_long(repo_script, timeout=900)
    with ctx.step(1):
        ctx.run_long(f"apt-get install -y {pkg}", timeout=3600)
    with ctx.step(2):
        env_script = (
            "sudo tee /etc/profile.d/vllm-cuda.sh >/dev/null <<'VDBEOF'\n"
            "export CUDA_HOME=/usr/local/cuda\n"
            "export PATH=$CUDA_HOME/bin:$PATH\n"
            "export LD_LIBRARY_PATH=$CUDA_HOME/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\n"
            "VDBEOF\n"
        )
        ctx.run_long(env_script, timeout=120)
    with ctx.step(3):
        # CUDA_VERSION_PROBE prints the version nvcc actually reports
        # ("release X.Y") — the old check looked for "CUDA Version", which
        # nvcc -V never prints, so every install failed verification.
        verify = ctx.exec(CUDA_VERSION_PROBE, timeout=30)
        found = (verify.get("stdout") or "").strip()
        if not verify.get("success") or found in ("", "NO_NVCC"):
            raise DeploymentError(
                f"nvcc verification failed: {(verify.get('stdout') or verify.get('stderr')).strip()}"
            )
        deployment_state.set_snapshot("cuda", installed_version=found,
                                      toolkit_path="/usr/local/cuda")
    ctx.log(f"CUDA toolkit {found or version} installed and verified.")


def _build_vllm_install_commands(payload: dict[str, Any], venv_path: str,
                                 mirrors: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Return (prepare_cmd, install_cmd, optional_dep_cmds)."""
    py_bin = f"{venv_path}/bin/python"
    mirror = (mirrors.get("pypi") or "").strip()
    index_args = f"--index-url {mirror}" if mirror else ""
    py_version = (payload.get("python_version") or "").strip()

    if payload.get("env_mode") == "new":
        py_args = f" --python {py_version}" if PY_VER_RE.fullmatch(py_version) else ""
        prepare = f"{UV_PATH_PREFIX}uv venv{py_args} {venv_path}"
    else:
        prepare = (
            f"test -d {venv_path} && test -x {py_bin} || "
            f"{{ echo 'virtualenv not found: {venv_path}' >&2; exit 1; }}"
        )

    version = (payload.get("version") or "latest").strip()
    spec = "vllm" if version in ("", "latest") else f"vllm=={version}"

    # Every branch below invokes uv, so each carries UV_PATH_PREFIX — a
    # bare `uv` fails in the non-login SSH shell when uv lives in
    # ~/.local/bin (see deployment_ops.UV_PATH_PREFIX).
    if payload.get("source_build"):
        install = (
            UV_PATH_PREFIX + "git clone --depth 1 https://gh-proxy.com/https://github.com/vllm-project/vllm.git /tmp/vllm-src-vdb "
            + f"&& uv pip install {index_args} --python {py_bin} -e /tmp/vllm-src-vdb".replace("  ", " ")
        )
    elif payload.get("runtime_mode") == "system":
        cuda_version = (payload.get("cuda_version") or "").strip()
        torch_index = (
            f" --extra-index-url https://download.pytorch.org/whl/cu{cuda_version.replace('.', '')}"
            if cuda_version else ""
        )
        install = (
            UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} {spec} torch{torch_index}".replace("  ", " ")
        )
    else:
        install = UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} {spec}"

    deps: list[str] = []
    if payload.get("flashinfer"):
        deps.append(UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} flashinfer-python")
    if payload.get("nccl"):
        deps.append(UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} nvidia-nccl-cu12")
    return prepare, install, deps


def _run_vllm(ctx: TaskContext, payload: dict[str, Any], venv_path: str,
              previous_version: str) -> None:
    mirrors = deployment_state.get()["mirrors"]
    prepare, install, deps = _build_vllm_install_commands(payload, venv_path, mirrors)

    with ctx.step(0):
        stop = ctx.executor.stop_vllm(force=False)
        if stop.get("success"):
            ctx.log(f"vLLM stopped (mode: {stop.get('mode')})")
        else:
            ctx.log(f"warning: stop_vllm: {stop.get('error')}")
    with ctx.step(1):
        ctx.run_long(prepare, timeout=900)
    with ctx.step(2):
        ctx.run_long(install, timeout=7200)
    with ctx.step(3):
        for dep_cmd in deps:
            ctx.run_long(dep_cmd, timeout=1800)
        if payload.get("mtp"):
            ctx.log("MTP speculative decoding is built into vLLM — no extra dependency.")
        if payload.get("rust_frontend"):
            ctx.log("Rust frontend ships inside the vLLM main package — no separate install.")
        if not deps and not payload.get("mtp") and not payload.get("rust_frontend"):
            ctx.log("No optional dependencies selected.")
    with ctx.step(4):
        verify = ctx.exec(
            f"bash -lc 'source {venv_path}/bin/activate 2>/dev/null; "
            f"vllm --version 2>&1 | head -n 3; python -c \"import vllm; print(vllm.__version__)\"'",
            timeout=120,
        )
        stdout = verify.get("stdout") or ""
        m = re.search(r"(\d+\.\d+\.\d+)", stdout)
        if not verify.get("success") or not m:
            raise DeploymentError(
                f"vLLM verification failed: {stdout.strip() or verify.get('stderr') or 'no output'}"
            )
        version = m.group(1)
        deployment_state.set_snapshot("vllm", version=version,
                                      previous_version=previous_version,
                                      venv=payload.get("venv_name", ""))
        ctx.log(f"vLLM {version} verified in {venv_path}.")

    if payload.get("auto_register_service") and len(ctx.task.steps) > 5:
        with ctx.step(5):
            sup_script = (
                "sudo mkdir -p /var/log/vllm; "
                "sudo tee /etc/supervisor/conf.d/vllm-vdb.conf >/dev/null <<'VDBEOF'\n"
                "[program:vllm]\n"
                f"command={venv_path}/bin/vllm serve <MODEL_PATH> --port 8000\n"
                "autostart=false\n"
                "autorestart=false\n"
                "redirect_stderr=true\n"
                "stdout_logfile=/var/log/vllm/vllm.log\n"
                "VDBEOF\n"
                "if command -v supervisorctl >/dev/null 2>&1; then "
                "sudo supervisorctl reread && sudo supervisorctl update; "
                "else echo 'supervisorctl not detected: template written only, configure manually'; fi"
            )
            ctx.run_long(sup_script, timeout=300)
            ctx.log("supervisor template written (autostart=false — edit the model path, then enable).")


def _run_reboot(ctx: TaskContext) -> None:
    with ctx.step(0):
        result = ctx.executor.reboot_server()
        if not result.get("success"):
            raise DeploymentError(result.get("error") or "Reboot command failed")
        ctx.log("Reboot command issued. The server should be back within 1-3 minutes.")


def _run_conflict_cleanup(ctx: TaskContext, packages: list[str]) -> None:
    quoted = " ".join(packages)  # pre-validated names, safe for apt
    with ctx.step(0):
        ctx.run_long(f"apt-get remove --purge -y {quoted} && apt-get autoremove -y",
                     timeout=1800)


def _run_rollback_vllm(ctx: TaskContext, target_version: str, venv_path: str,
                       mirrors: dict[str, Any]) -> None:
    py_bin = f"{venv_path}/bin/python"
    mirror = (mirrors.get("pypi") or "").strip()
    index_args = f"--index-url {mirror}" if mirror else ""
    with ctx.step(0):
        stop = ctx.executor.stop_vllm(force=False)
        if stop.get("success"):
            ctx.log(f"vLLM stopped (mode: {stop.get('mode')})")
        else:
            ctx.log(f"warning: stop_vllm: {stop.get('error')}")
    with ctx.step(1):
        ctx.run_long((UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} vllm=={target_version}").replace("  ", " "),
                     timeout=7200)
    with ctx.step(2):
        verify = ctx.exec(
            f"bash -lc 'source {venv_path}/bin/activate 2>/dev/null; "
            f"python -c \"import vllm; print(vllm.__version__)\"'",
            timeout=120,
        )
        stdout = verify.get("stdout") or ""
        if not verify.get("success") or target_version not in stdout:
            raise DeploymentError(
                f"Rollback verification failed: {stdout.strip() or verify.get('stderr') or 'no output'}"
            )
        deployment_state.set_snapshot("vllm", version=target_version)
        ctx.log(f"vLLM rolled back to {target_version}.")


# ===========================================================================
# Public start_* entry points (called by the API layer)
# ===========================================================================

def start_driver_task(package: str) -> Task:
    _require_remote()
    if not DRIVER_PKG_RE.fullmatch(package or ""):
        raise DeploymentError(f"Invalid driver package name: {package}")
    executor = get_remote_executor()
    info = executor.get_driver_list()
    if not info.get("success"):
        raise DeploymentError(info.get("error") or "Failed to query driver list")
    target_major: int | None = None
    for row in info["drivers"]:
        if row["package"] == package:
            target_major = row["version"]
            break
    if target_major is None:
        raise DeploymentError(f"Driver {package} is not in the available list")

    installed = executor.list_installed_driver_packages()
    current_pkg = installed["packages"][0]["name"] if installed.get("packages") else ""
    current_major = _major_driver_version(info.get("current_driver", ""))
    recorded = deployment_state.get()["snapshots"]["driver"]["current_pkg"]
    deployment_state.set_snapshot(
        "driver",
        previous_pkg=current_pkg or recorded,
        previous_version=str(current_major) if current_major else "",
    )
    deployment_state.update_section("selected", driver=package)

    task = task_manager.create("driver", f"Apply driver {package}",
                               ["Stop vLLM service", "Uninstall old driver",
                                "Install target driver", "Wait for reboot"])
    task.result = {"package": package, "target_major": target_major,
                   "current_driver": info.get("current_driver", "")}
    task_manager.submit(task, lambda ctx: _run_driver(ctx, package, target_major))
    return task


def start_cuda_task(version: str, install_system: bool) -> Task | None:
    _require_remote()
    if not CUDA_VER_RE.fullmatch(version or ""):
        raise DeploymentError(f"Invalid CUDA version: {version}")
    executor = get_remote_executor()
    info = executor.get_driver_list()
    if not info.get("success"):
        raise DeploymentError(info.get("error") or "Failed to query driver version")
    driver_major = _major_driver_version(info.get("current_driver", ""))
    problem = check_cuda_compat(driver_major, version)
    if problem:
        raise DeploymentError(problem)

    deployment_state.update_section("selected", cuda=version,
                                    cuda_install_system=install_system)
    if not install_system:
        return None  # selection saved; no mutation needed

    pkg = f"cuda-toolkit-{version.replace('.', '-')}"
    task = task_manager.create("cuda", f"Install CUDA toolkit {version}",
                               ["Add NVIDIA CUDA repo", f"Install {pkg}",
                                "Write CUDA environment", "Verify nvcc"])
    task.result = {"version": version, "package": pkg}
    task_manager.submit(task, lambda ctx: _run_cuda(ctx, version, pkg))
    return task


def start_vllm_task(payload: dict[str, Any]) -> Task:
    _require_remote()
    version = (payload.get("version") or "latest").strip()
    if version != "latest" and not VLLM_VER_RE.fullmatch(version):
        raise DeploymentError(f"Invalid vLLM version: {version}")
    if payload.get("runtime_mode") not in ("builtin", "system"):
        raise DeploymentError("Invalid CUDA runtime mode")
    if payload.get("env_mode") not in ("new", "existing"):
        raise DeploymentError("Invalid Python environment mode")
    venv_name = (payload.get("venv_name") or "").strip() or config_manager.get_config().venv_name or ".vllm"
    _validate_venv_name(venv_name)
    py_version = (payload.get("python_version") or "").strip()
    if py_version and not PY_VER_RE.fullmatch(py_version):
        raise DeploymentError(f"Invalid Python version: {py_version}")

    state = deployment_state.get()
    cuda_version = (payload.get("cuda_version") or state["selected"].get("cuda") or "").strip()
    if payload.get("runtime_mode") == "system" and not cuda_version:
        raise DeploymentError("System CUDA runtime requires a CUDA version selected in Tab 2")
    payload = {**payload, "venv_name": venv_name, "python_version": py_version,
               "cuda_version": cuda_version}

    executor = get_remote_executor()
    env_info = executor.get_vllm_env()
    previous_version = env_info.get("vllm_version", "") if env_info.get("success") else ""

    deployment_state.update_section("selected", vllm_version=version,
                                    vllm_runtime=payload["runtime_mode"])
    venv_path = _resolve_venv_path(venv_name)
    title = (f"Install vLLM {version}" if version != "latest" else "Install vLLM (latest)")
    steps = ["Stop vLLM service", "Prepare Python environment", "Install vLLM",
             "Install optional dependencies", "Verify version"]
    if payload.get("auto_register_service"):
        steps.append("Write supervisor template")
    task = task_manager.create("vllm", title, steps)
    task.result = {"version": version, "venv": venv_name, "previous_version": previous_version}
    task_manager.submit(task, lambda ctx: _run_vllm(ctx, payload, venv_path, previous_version))
    return task


def start_rollback_driver() -> Task:
    _require_remote()
    state = deployment_state.get()
    previous = (state["snapshots"]["driver"].get("previous_pkg") or "").strip()
    if not previous:
        raise DeploymentError("No previous driver recorded — nothing to roll back to")
    if not DRIVER_PKG_RE.fullmatch(previous):
        raise DeploymentError(f"Invalid recorded driver package: {previous}")
    return start_driver_task(previous)


def start_rollback_vllm() -> Task:
    _require_remote()
    state = deployment_state.get()
    target = (state["snapshots"]["vllm"].get("previous_version") or "").strip()
    if not target:
        raise DeploymentError("No previous vLLM version recorded — nothing to roll back to")
    if not VLLM_VER_RE.fullmatch(target):
        raise DeploymentError(f"Invalid recorded vLLM version: {target}")
    venv_name = (state["snapshots"]["vllm"].get("venv")
                 or config_manager.get_config().venv_name or ".vllm")
    _validate_venv_name(venv_name)
    venv_path = _resolve_venv_path(venv_name)
    task = task_manager.create("rollback_vllm", f"Roll back vLLM to {target}",
                               ["Stop vLLM service", f"Reinstall vllm=={target}", "Verify version"])
    task.result = {"target_version": target, "venv": venv_name}
    task_manager.submit(
        task,
        lambda ctx: _run_rollback_vllm(ctx, target, venv_path, deployment_state.get()["mirrors"]),
    )
    return task


def start_reboot_task() -> Task:
    _require_remote()
    task = task_manager.create("reboot", "Reboot server", ["Reboot server"])
    task_manager.submit(task, _run_reboot)
    return task


def start_conflict_cleanup(packages: list[str]) -> Task:
    _require_remote()
    if not packages or len(packages) > 64:
        raise DeploymentError("Invalid package list")
    for name in packages:
        if not PKG_NAME_RE.fullmatch(name or ""):
            raise DeploymentError(f"Invalid package name: {name}")
    task = task_manager.create("conflict_cleanup",
                               f"Remove {len(packages)} redundant package(s)",
                               ["Purge packages"])
    task.result = {"packages": list(packages)}
    task_manager.submit(task, lambda ctx: _run_conflict_cleanup(ctx, list(packages)))
    return task


def reconcile_driver_task() -> Task | None:
    """Verify a post-reboot driver install (called from read endpoints)."""
    task = task_manager.pending_driver_task()
    if task is None:
        return None
    try:
        executor = get_remote_executor()
        result = executor.execute(
            "nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1",
            timeout=15,
        )
    except Exception as e:
        logger.info("driver reconcile skipped (server not reachable): %s", e)
        return task
    if not result.get("success"):
        return task
    current_major = _major_driver_version((result.get("stdout") or "").strip())
    target_major = task.result.get("target_major")
    if current_major is not None and target_major is not None and current_major == target_major:
        task_manager.complete_driver(task)
        deployment_state.set_snapshot("driver",
                                      current_pkg=task.result.get("package", ""),
                                      previous_version=str(current_major))
        task_manager.log(task.id, f"Reboot verified: driver {current_major} active. Task complete.")
    return task


# Module singletons
deployment_state = DeploymentState()
task_manager = DeploymentTaskManager()
