"""DeploymentTaskManager: async mutation tasks with persisted records.

Split out of ``deployment_service.py`` (file-size gate). Reattach / remote
job control lives in ``deployment_reattach.py`` (mixed in below).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime

from . import deployment_service as _ds
from .deployment_common import DeploymentError, TaskCancelled, _now_iso, _suggest_error
from .deployment_reattach import ReattachMixin
from .deployment_task_ctx import Task, TaskContext

logger = logging.getLogger(__name__)


class DeploymentTaskManager(ReattachMixin):
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
        if not os.path.exists(_ds.TASKS_FILE):
            return
        try:
            with open(_ds.TASKS_FILE, encoding="utf-8") as f:
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
        self._reattach_interrupted()

    def _persist_locked(self) -> None:
        entries = [t.to_dict() for t in list(self._tasks.values())[-self.MAX_TASKS:]]
        os.makedirs(_ds.DATA_DIR, exist_ok=True)
        tmp = _ds.TASKS_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)
            os.replace(tmp, _ds.TASKS_FILE)
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
        _ds.deployment_state.push_history({
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
        # created_at (fixed-format, same clock) orders deterministically —
        # dict insertion order flips after a restart re-reads the task file.
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def pending_driver_task(self) -> Task | None:
        with self._lock:
            candidates = [t for t in self._tasks.values()
                          if t.kind == "driver" and t.status == "awaiting_reboot"]
        return max(candidates, key=lambda t: t.created_at) if candidates else None

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            event = self._cancel.get(task_id)
            if task is None or event is None or task.status not in ("queued", "running"):
                return False
            event.set()
            return True

    def wait_terminal(self, task_id: str, timeout: float = 30.0) -> bool:
        """Join the task's worker thread; True if it has fully exited.

        A terminal status alone does not guarantee every file write for the
        task has landed (the final persist / history push run after the
        status flip) — callers that assert on persisted state or tear down
        the data dir (tests) should join first.
        """
        with self._lock:
            thread = self._threads.get(task_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

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
            os.makedirs(_ds.LOG_DIR, exist_ok=True)
            with open(os.path.join(_ds.LOG_DIR, f"{task_id}.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.warning("failed to append deployment log for %s: %s", task_id, e)

    def tail(self, task_id: str, n: int = 200) -> list[str]:
        with self._lock:
            tail = self._tails.get(task_id)
            return list(tail)[-n:] if tail else []

    def file_tail(self, task_id: str, n: int = 12) -> list[str]:
        """Last n log lines from the log file (in-memory tail is empty after restarts)."""
        path = os.path.join(_ds.LOG_DIR, f"{task_id}.log")
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 8192))
                data = f.read()
        except OSError:
            return []
        lines = data.decode("utf-8", "replace").splitlines()
        if size > 8192 and lines:
            lines = lines[1:]  # drop the possibly truncated first line
        return lines[-n:]

    def read_log(self, task_id: str, offset: int = 0) -> tuple[str, int]:
        """Log content from byte ``offset`` (incremental fetch) + new offset."""
        with self._lock:
            if task_id not in self._tasks:
                return "", offset
        path = os.path.join(_ds.LOG_DIR, f"{task_id}.log")
        try:
            with open(path, "rb") as f:
                f.seek(max(0, offset))
                data = f.read(self.LOG_CHUNK)
        except OSError:
            return "", offset
        return data.decode("utf-8", "replace"), max(0, offset) + len(data)
