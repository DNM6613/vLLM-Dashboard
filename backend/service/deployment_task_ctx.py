"""Deployment task record and per-task execution context.

Split out of ``deployment_service.py`` (file-size gate).
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager, suppress
from typing import Any

from . import deployment_service as _ds
from .deployment_common import DeploymentError, TaskCancelled, _now_iso

logger = logging.getLogger(__name__)


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


class TaskContext:
    """Per-task plumbing: executor, logging, steps, sudo, long-running steps."""

    def __init__(self, task: Task, manager) -> None:
        self.task = task
        self.manager = manager
        self.executor = _ds.get_remote_executor()
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
        # Persist the remote job artifacts: on a dashboard restart the task
        # manager re-attaches a still-live job, and cancellation needs the
        # pid file to kill the remote process tree.
        with self.manager._lock:
            self.task.result["job_files"] = {
                "log": log_file, "pid": pid_file, "exit": exit_file,
            }
            self.manager._persist_locked()

        marker = "VDB_POLL"
        offset = 0
        deadline = time.monotonic() + timeout
        gone_polls = 0
        while True:
            try:
                self.check_cancelled()
            except TaskCancelled:
                # The local watcher dies with this thread; the remote nohup
                # job would keep mutating the server (apt locks, half
                # installs) — kill its whole process tree via the pid file.
                self.manager._kill_long(self.task.id, pid_file)
                self._cleanup_long(pw_file, log_file, pid_file, exit_file)
                with self.manager._lock:
                    self.task.result.pop("job_files", None)
                    self.manager._persist_locked()
                raise
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
                with self.manager._lock:
                    self.task.result.pop("job_files", None)
                    self.manager._persist_locked()
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
