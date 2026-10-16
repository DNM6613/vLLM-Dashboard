"""Model download status parsing (log tail, byte-accurate progress, stall).

Split out of ``model_downloads.py`` (file-size gate). Shared download state
(``_dl_total_cache`` / ``_dl_growth``) and the executor hook live on the
``model_service`` facade and are resolved through it.
"""

from __future__ import annotations

import logging
import re
import shlex
import time

from ..executor.remote.model_ops import DL_PROBE_SENTINEL, DL_PROCESS_PATTERN
from . import model_service as _ms

logger = logging.getLogger(__name__)


def _build_download_status_command(log_file: str, repo: str = "") -> str:
    safe_log = shlex.quote(log_file)
    pid_file = shlex.quote(log_file + ".pid")
    # Match the download process for this specific repo so a second concurrent
    # download is not mistaken for this one.
    repo_filter = f"| grep -F {shlex.quote(repo)} " if repo else ""
    dl_pids = (
        "ps -eo pid=,args= 2>/dev/null "
        f"| grep -v '{DL_PROBE_SENTINEL}' "
        f"| grep -E '{DL_PROBE_SENTINEL}|{DL_PROCESS_PATTERN}' "
        f"{repo_filter}"
        "| awk '{print $1}' "
        "| head -1"
    )
    # The pid file (written when the download started) is authoritative: it
    # names the exact wrapper process and survives a backend restart. The
    # ps|grep fallback covers a missing/stale pid file.
    pid_expr = (
        f"PF={pid_file}; WP=$(cat \"$PF\" 2>/dev/null); "
        f"if [ -n \"$WP\" ] && kill -0 \"$WP\" 2>/dev/null; then echo \"$WP\"; "
        f"else {dl_pids}; fi"
    )
    return (
        f"if [ -f {safe_log} ]; then "
        f"echo EXISTS; "
        f"echo \"SIZE=$(wc -c < {safe_log} 2>/dev/null)\"; "
        f"echo \"MTIME=$(stat -c %Y {safe_log} 2>/dev/null)\"; "
        f"echo \"PID=$({pid_expr})\"; "
        f"echo '---TAIL---'; "
        f"tail -200 {safe_log}; "
        f"else echo NOT_FOUND; fi"
    )


def _get_download_total(executor, model_repo: str, hf_mirror: bool) -> int:
    now = time.time()
    entry = _ms._dl_total_cache.get(model_repo)
    if entry is not None and now - entry[0] < _ms.DL_TOTAL_TTL_SECS:
        return entry[1]
    total = 0
    try:
        total = int(executor.get_download_total_size(model_repo, hf_mirror).get("total_bytes", 0) or 0)
    except Exception as e:
        logger.warning(f"Failed to fetch total size for {model_repo}: {e}")
    _ms._dl_total_cache[model_repo] = (now, total)
    return total


def get_download_status(log_file: str, repo: str = "", save_path: str = "",
                        hf_mirror: bool = True) -> dict:
    executor = _ms.get_remote_executor()
    cmd = _build_download_status_command(log_file, repo)
    result = executor.execute(cmd)
    if not result["success"]:
        return {
            "status": "unknown",
            "log": "",
            "message": "Failed to read download log"
        }

    stdout = result["stdout"]
    if stdout.startswith("NOT_FOUND"):
        return {
            "status": "not_found",
            "log": "",
            "message": "Download log file not found"
        }

    head, _sep, log_content = stdout.partition("---TAIL---\n")
    log_size = 0
    log_mtime = 0
    pid = ""
    for line in head.splitlines():
        if line.startswith("SIZE="):
            v = line[5:].strip()
            log_size = int(v) if v.isdigit() else 0
        elif line.startswith("MTIME="):
            v = line[6:].strip()
            log_mtime = int(v) if v.isdigit() else 0
        elif line.startswith("PID="):
            pid = line[4:].strip()
    is_running = pid.isdigit()

    # Real on-disk bytes of the download target — makes progress/stall visible
    # even when the CLI's textual progress is misleading or truncated.
    size_bytes = 0
    if repo:
        try:
            size_bytes = executor.get_download_size(repo, save_path).get("size_bytes", 0)
        except Exception as e:
            logger.warning(f"Failed to read download size for {repo}: {e}")

    # Repo total (HF API usedStorage): denominator for byte-accurate progress.
    # The CLI's textual % is a *file count* ratio — 16 small files reach
    # "89%" in seconds while the remaining 3 x 10GB files take 10 more minutes.
    total_size = _get_download_total(executor, repo, hf_mirror) if repo else 0

    has_fatal_error = False
    for line in log_content.split("\n"):
        if ("ERROR: hf not found" in line or
            "ERROR: huggingface-cli not found" in line or
            "command not found" in line or
            "Traceback" in line or "Exception" in line or
            "OSError" in line or "ConnectionError" in line or
            "is deprecated and no longer works" in line or
            # Gated repo without an approved HF token: `hf` exits with
            # "Error: Access denied. This repository requires approval."
            "Access denied" in line or "requires approval" in line):
            has_fatal_error = True
            break

    complete_marker = bool(re.search(r'/models--[\w.()-]+/snapshots/[\w]+', log_content))

    progress_matches = re.findall(r'(\d+(?:\.\d+)?)\s*%', log_content)
    parsed_progress = float(progress_matches[-1]) if progress_matches else None

    def _progress() -> float:
        # Byte ratio first (truthful), textual % second (file count — only
        # meaningful when the total is unknown), elapsed-time estimate last.
        if total_size > 0 and size_bytes > 0:
            return min(99.0, size_bytes / total_size * 100.0)
        if parsed_progress is not None:
            return min(99.0, parsed_progress)
        if log_mtime > 0:
            elapsed = max(1, time.time() - log_mtime)
            return min(95.0, max(5.0, (elapsed / 1800) * 100.0))
        return 5.0

    if is_running:
        # Stall detection on byte growth, not log mtime: the `hf` CLI writes
        # one line per *file* to a redirected log, so the log sits silent for
        # minutes while a large file transfers — that is not a stall.
        now = time.time()
        prev = _ms._dl_growth.get(log_file)
        if prev is None or pid != prev[0] or size_bytes > prev[1]:
            _ms._dl_growth[log_file] = (pid, size_bytes, now)
            stalled_secs = 0
        elif size_bytes > 0:
            stalled_secs = int(now - prev[2])
        else:
            # No bytes on disk yet (file list resolution): fall back to the
            # log mtime so a hung pre-transfer phase is still caught.
            stalled_secs = int(now - log_mtime) if log_mtime > 0 else 0
        progress = _progress()
        if stalled_secs > _ms.DL_STALL_THRESHOLD_SECS:
            return {
                "status": "stalled",
                "progress": round(progress, 1),
                "log": log_content,
                "size_bytes": size_bytes,
                "total_size": total_size,
                "stalled_secs": stalled_secs,
                "message": f"Download stalled ({int(stalled_secs // 60)} min without byte progress)"
            }
        return {
            "status": "downloading",
            "progress": round(progress, 1),
            "log": log_content,
            "size_bytes": size_bytes,
            "total_size": total_size,
            "message": f"Downloading... ({log_size / 1024:.1f}KB log)"
        }

    _ms._dl_growth.pop(log_file, None)

    if complete_marker:
        return {
            "status": "complete",
            "log": log_content,
            "size_bytes": size_bytes,
            "total_size": total_size,
            "message": "Download complete"
        }

    if has_fatal_error:
        reason_lines = [
            line.strip() for line in log_content.split("\n")
            if ("ERROR" in line or "Traceback" in line or "Exception" in line
                    or "command not found" in line or "OSError" in line
                    or "ConnectionError" in line or "not found" in line
                    or "Access denied" in line)
        ]
        reason = " | ".join(reason_lines[-3:])[:500]
        return {
            "status": "failed",
            "log": log_content,
            "size_bytes": size_bytes,
            "total_size": total_size,
            "message": "Download failed",
            "reason": reason
        }

    # Process exited with no completion marker and no detected fatal error:
    # the download was interrupted (kill, crash, network drop). Do not report
    # "complete" — the model may be partial.
    return {
        "status": "stopped",
        "log": log_content,
        "size_bytes": size_bytes,
        "total_size": total_size,
        "message": "Download stopped before completion"
    }


def stop_download(repo: str) -> dict:
    executor = _ms.get_remote_executor()
    result = executor.stop_download(repo)
    return {
        "status": "success",
        "killed": result.get("killed", False),
        "no_process": result.get("no_process", False),
    }
