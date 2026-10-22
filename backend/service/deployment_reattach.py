"""DeploymentTaskManager restart-reattach and remote-job control helpers.

Split out of ``deployment_service.py`` (file-size gate). Mixed into
``DeploymentTaskManager`` (see ``deployment_task_manager.py``); every method
expects ``self`` to be the task manager.
"""

from __future__ import annotations

import logging
import shlex
import threading
import time
from contextlib import suppress

from . import deployment_service as _ds
from .deployment_common import _now_iso, tail_offset

logger = logging.getLogger(__name__)


class ReattachMixin:
    """Dashboard-restart reattach: a remote nohup job may still be running
    when the dashboard comes back; watch it instead of declaring it dead."""

    def _reattach_interrupted(self) -> None:
        """A dashboard restart does not stop the remote nohup job. If the
        job's pid is still alive the task was not really interrupted — flip
        it back to running and watch the job to completion."""
        candidates: list[tuple] = []
        with self._lock:
            for task in self._tasks.values():
                if task.status != "interrupted":
                    continue
                job = task.result.get("job_files")
                if isinstance(job, dict) and job.get("pid"):
                    candidates.append((task, dict(job)))
        if not candidates:
            return

        def worker() -> None:
            try:
                executor = _ds.get_remote_executor()
                # Audit 2026-10-20 P3-1: `kill -0` alone misjudges a reused
                # pid as live. Confirm the process is really our job by
                # checking its /proc/<pid>/cmdline carries the job's log
                # path (the wrapper writes `> .../vdb_deploy_<tid>.log.exit`,
                # a superset of the log base name `B` below), so a recycled
                # pid whose cmdline lacks it is not re-attached.
                probe = executor.execute(
                    "for f in "
                    + " ".join(shlex.quote(job["pid"]) for _, job in candidates)
                    + "; do P=$(cat \"$f\" 2>/dev/null); [ -n \"$P\" ] || continue; "
                      "kill -0 \"$P\" 2>/dev/null || continue; "
                      "B=${f%.log.pid}; tr '\\0' ' ' < \"/proc/$P/cmdline\" 2>/dev/null | "
                      "grep -qF \"$B\" && echo \"LIVE:$f\"; done; echo DONE",
                    timeout=30)
            except Exception as e:
                logger.warning("deploy reattach probe failed (staying interrupted): %s", e)
                return
            live = {line.split(":", 1)[1]
                    for line in (probe.get("stdout") or "").splitlines()
                    if line.startswith("LIVE:")}
            for task, job in candidates:
                if job["pid"] not in live:
                    continue
                with self._lock:
                    task.status = "running"
                    task.error = ""
                    self._cancel[task.id] = threading.Event()
                    self._persist_locked()
                self.log(task.id,
                         "Dashboard restarted; remote step still running — "
                         "watching it to completion")
                self._watch_remote_job(task, job)

        thread = threading.Thread(target=worker, name="deploy-reattach", daemon=True)
        thread.start()

    def _watch_remote_job(self, task, job: dict[str, str]) -> None:
        """Tail a pre-existing remote job to completion (restart reattach).
        The pipeline cannot be resumed mid-way, so the task finalizes with a
        re-run instruction once the job settles."""
        executor = _ds.get_remote_executor()
        log_file, pid_file, exit_file = job["log"], job["pid"], job["exit"]
        # Audit 2026-10-22 REM-6: the sudo password temp file is part of the
        # job's remote footprint — every terminal branch must delete it (it
        # is chmod 600 plaintext and must not outlive the job). Older jobs
        # (pre-REM-6) have no "pw" key; _cleanup's rm -f tolerates the miss.
        pw_file = job.get("pw", "")
        cleanup_paths = [p for p in (log_file, pid_file, exit_file, pw_file) if p]
        marker = "VDB_POLL"
        offset = 0
        gone_polls = 0
        deadline = time.monotonic() + 6 * 3600
        while True:
            with self._lock:
                event = self._cancel.get(task.id)
            if event is not None and event.is_set():
                self._kill_long(task.id, pid_file)
                self._cleanup(task.id, *cleanup_paths)
                with self._lock:
                    task.status = "cancelled"
                    task.finished_at = _now_iso()
                    for step in task.steps:
                        if step["status"] in ("pending", "running"):
                            step["status"] = "skipped"
                    self._persist_locked()
                self._record_history(task)
                return
            try:
                poll = executor.execute(
                    f"tail -c +{offset + 1} {log_file} 2>/dev/null; echo; echo {marker}; "
                    f"if [ -f {exit_file} ]; then cat {exit_file}; "
                    f"elif kill -0 $(cat {pid_file} 2>/dev/null) 2>/dev/null; then echo VDB_RUNNING; "
                    f"else echo VDB_GONE; fi",
                    timeout=30,
                )
            except Exception as e:
                self.log(task.id, f"reattach: transient error ({e}); retrying")
                time.sleep(5)
                continue
            out = poll.get("stdout") or ""
            idx = out.rfind(f"\n{marker}\n")
            content = out[: idx + 1] if idx >= 0 else out
            status = out[idx + len(marker) + 1:].strip() if idx >= 0 else ""
            if content:
                for line in content.splitlines():
                    if line.strip():
                        self.log(task.id, line)
                offset += tail_offset(content)
            if status.startswith("VDB_EXIT:"):
                try:
                    code = int(status.split(":", 1)[1])
                except ValueError:
                    code = -1
                # Audit 2026-10-22 REM-6: clean the remote job files (incl. the
                # sudo password temp) on the terminal branch — the job has
                # settled, so nothing needs them any more.
                self._cleanup(task.id, *cleanup_paths)
                self._finalize_reattached(task, code)
                return
            if status == "VDB_RUNNING":
                gone_polls = 0
            elif status == "VDB_GONE":
                gone_polls += 1
                if gone_polls >= 3:
                    # Audit 2026-10-22 REM-6: clean the remote job files
                    # (incl. the sudo password temp) before finalizing.
                    self._cleanup(task.id, *cleanup_paths)
                    self._finalize_reattached(task, None)
                    return
            if time.monotonic() > deadline:
                # Audit 2026-10-22 REM-3: align with run_long's deadline
                # branch (deployment_task_ctx) — a reattached job that
                # outlives the 6h watch window must be terminated, not just
                # abandoned: it still holds the GPU / apt locks remotely.
                self._kill_long(task.id, pid_file)
                self._cleanup(task.id, *cleanup_paths)
                self._finalize_reattached(task, None)
                return
            time.sleep(2.5)

    def _finalize_reattached(self, task, code: int | None) -> None:
        # Audit 2026-10-20 P2-5 (user ruling): the status stays "failed"
        # (the pipeline's remaining steps did not run — calling it a
        # success would mislead), but the message must not blur a cleanly
        # finished remote step into a failure.
        if code == 0:
            detail = ("completed successfully (code 0) but the pipeline was "
                      "not resumed — the remaining steps did not run")
        elif code is not None:
            detail = f"exited with code {code} and the pipeline was not resumed"
        else:
            detail = "exited (unknown code) and the pipeline was not resumed"
        with self._lock:
            task.status = "failed"
            task.error = (
                f"Dashboard restarted mid-task; the remote step {detail} — "
                f"re-run the task to finish"
            )
            task.suggestion = ""
            task.finished_at = _now_iso()
            task.result.pop("job_files", None)
            self._persist_locked()
        self._record_history(task)

    def _kill_long(self, task_id: str, pid_file: str) -> None:
        """Kill the remote nohup job's whole process tree (wrapper bash →
        sudo → apt). The wrapper is not a process-group leader, so walk the
        process tree recursively instead of a group signal.

        Audit 2026-10-22 REM-12: re-check /proc/<pid>/cmdline still carries
        the job's log base name before killing — the pid file is never
        removed, so a recycled pid (a long-lived process reusing the number)
        would otherwise have its whole tree killed. Mirrors the reattach
        probe's cmdline re-check; the wrapper's own cmdline references the
        exit file (a superset of the base name), so a live job always
        matches and a recycled pid never does.
        """
        with suppress(Exception):
            _ds.get_remote_executor().execute(
                f"PFILE={shlex.quote(pid_file)}; P=$(cat \"$PFILE\" 2>/dev/null); "
                "B=${PFILE%.log.pid}; "
                "if [ -n \"$P\" ] && kill -0 \"$P\" 2>/dev/null && "
                "tr '\\0' ' ' < \"/proc/$P/cmdline\" 2>/dev/null | grep -qF \"$B\"; then "
                "kterm() { for c in $(ps -o pid= --ppid \"$1\" 2>/dev/null); do kterm \"$c\"; done; kill -TERM \"$1\" 2>/dev/null; }; "
                "kkill() { for c in $(ps -o pid= --ppid \"$1\" 2>/dev/null); do kkill \"$c\"; done; kill -KILL \"$1\" 2>/dev/null; }; "
                "kterm \"$P\"; sleep 2; kkill \"$P\"; echo VDB_KILLED; else echo VDB_GONE; fi",
                timeout=25,
            )
            self.log(task_id, "Remote background step terminated")

    def _cleanup(self, task_id: str, *paths: str) -> None:
        with suppress(Exception):
            _ds.get_remote_executor().execute(f"rm -f {' '.join(paths)}", timeout=10)
