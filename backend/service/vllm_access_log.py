"""Per-IP aggregation of vLLM's own HTTP access log (接入 panel data source).

The panel must show who is talking to the AI server's vLLM — direct API
clients included — not just who calls the dashboard's own /api/v1 (a
browser opening the dashboard never touches vLLM; a direct client never
touches the dashboard).

Data source: vLLM's own uvicorn HTTP access log, landed on the AI server
by vLLM's native ``--logging-config.pylogging_config_file`` dictConfig (a
``RotatingFileHandler`` on the ``uvicorn.access`` logger writes
``VLLM_ACCESS_LOG``). No stdout hijacking, no proxy — the user's start
command is delivered verbatim plus that one logging flag, and vLLM's
console output is untouched (access lines still propagate to the console).
A poller thread tails the file over SSH and parses the access lines.
Instances started without the flag (e.g. a manual terminal launch) write
no such file, so the panel stays empty for them until the model is
(re)started from the dashboard.
"""

import contextlib
import json
import logging
import os
import re
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

# Written by vLLM itself (a RotatingFileHandler on the uvicorn.access
# logger, configured via --logging-config.pylogging_config_file) and tailed
# by this service. On the AI server's home dir (writable by uforce, rotated
# at 50MB so it stays bounded).
VLLM_ACCESS_LOG = "/home/uforce/vllm_logs/access.log"

POLL_INTERVAL_SECONDS = 3.0
MAX_ENTRIES = 2000
# Shell marker emitted when the file shrank below the PREVIOUS read
# position (the RotatingFileHandler rolled it over to access.log.1, or the
# file was deleted). 2026-10-24: the threshold is offset-1, NOT offset —
# after every poll the offset sits one past the file end, so `S < offset`
# fired on EVERY quiet poll where the file simply has not grown (e.g. the
# minutes-long vLLM reload window), wrongly resetting the aggregates to
# the full historical file.
TRUNC_MARKER = "VDB_LOG_TRUNC"

# Audit 2026-10-23 A-4: byte-count marker line emitted at the FRONT of
# every poll output (``wc -c`` of the chunk, in the C locale). The offset
# must advance by the RAW bytes read — the executor decodes with
# errors="replace", and an invalid-UTF-8 chunk turns 1 raw byte into 3
# bytes of U+FFFD, so the re-encoded text length drifts the offset and
# silently drops the next poll's lines.
BYTES_MARKER = "VDB_BYTES="
_BYTES_RE = re.compile(r"^VDB_BYTES=(\d+)\n")

# 2026-10-24 session semantics (user ruling): the panel stats restart on
# every vLLM service (re)start — the per-IP aggregates only cover the
# CURRENT vLLM instance. The session start point (the access log's byte
# size at the (re)start) is persisted under the data volume so a
# dashboard restart resumes the same session instead of re-reading the
# whole file (the pre-restart lines belong to the previous instance).
SESSION_FILE_NAME = "vllm_access_session.json"
# Remote-reported file size (bytes) line at the front of every poll output
# — lets a pending session start skip the file's pre-restart tail.
SIZE_MARKER = "VDB_SIZE="
_SIZE_RE = re.compile(r"^VDB_SIZE=(\d+)\n")

# vLLM's uvicorn access line as written to VLLM_ACCESS_LOG by the dictConfig
# formatter (ISO timestamp prefix, then client + request line + status):
#   2026-10-23T16:06:05 10.131.8.27:51234 - "POST /v1/chat/completions HTTP/1.1" 200
# The legacy console-wrapped format
#   INFO:     10.131.8.27:51234 - "POST /v1/chat/completions HTTP/1.1" 200 OK
# (and plain uvicorn's `IP:port - - [23/Oct/2026 11:45:12] "..." 200 OK`) is
# accepted too — the regex is not line-anchored, so any leading decoration
# is tolerated.
ACCESS_LINE_RE = re.compile(
    r'(?:^|\s)(?P<ip>\d{1,3}(?:\.\d{1,3}){3}):\d+\s+-\s+'
    r'(?:\S+\s+\[[^\]]*\]\s+)?'
    r'"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+"\s+(?P<status>\d{3})'
)

# ANSI escape sequences, stripped defensively before matching: any color
# code sitting right before the IP would break the `^|\s` boundary.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# The dashboard's own monitoring probes (health/metrics/model-list polls
# every few seconds) are not client access — filtering them keeps the panel
# about real clients.
_PROBE_PATHS = ("/health", "/metrics", "/v1/models")

# A \r-only stream (progress bars) never yields a \n; bound the carried
# partial line so it cannot grow without end.
_MAX_LINE_BUF = 65536


class VllmAccessLog:
    """Thread-safe ring of recent vLLM HTTP requests + per-IP aggregates.

    Mirrors the RequestLog public API so the /api/v1/monitor endpoints
    stay source-agnostic."""

    def __init__(self, maxlen: int = MAX_ENTRIES,
                 session_file: str | None = None):
        self._lock = threading.Lock()
        self._entries: deque[dict] = deque(maxlen=maxlen)
        self._by_ip: dict[str, dict] = {}
        # Next byte to read (1-based: `tail -c +N`); 1 = from the beginning.
        self._offset = 1
        # Partial line carried across poll chunks (a tail can split any line).
        self._line_buf = ""
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        # 2026-10-24: armed by mark_session_start(), consumed by the next
        # well-formed poll (only it knows the remote file size).
        self._skip_to_end = False
        # Persistence path for the session start point; None = resolve the
        # data volume path lazily in start_polling (avoids importing
        # deployment_service at module load — the audit 2026-10-23 batch-1
        # circular-import lesson).
        self._session_file = session_file

    # ---- recording API (same shape as RequestLog) ----

    def record(self, ip: str, method: str, path: str, status: int,
               body: str | None = None) -> None:
        ts = time.time()
        with self._lock:
            self._entries.append({
                "ts": ts, "ip": ip, "method": method,
                "path": path, "status": status, "body": body,
            })
            agg = self._by_ip.get(ip)
            if agg is None:
                agg = self._by_ip[ip] = {
                    "ip": ip, "count": 0,
                    "last_seen": ts, "last_method": method, "last_path": path,
                }
            agg["count"] += 1
            agg["last_seen"] = ts
            agg["last_method"] = method
            agg["last_path"] = path

    def connections(self, limit: int = 100) -> list[dict]:
        """Per-IP aggregates, most recently active first."""
        with self._lock:
            rows = [dict(a) for a in self._by_ip.values()]
        rows.sort(key=lambda a: a["last_seen"], reverse=True)
        return rows[:limit]

    def requests_for(self, ip: str, limit: int = 100) -> list[dict]:
        """Recent entries for one IP, newest first (bounded by the ring)."""
        with self._lock:
            rows = [e for e in reversed(list(self._entries)) if e["ip"] == ip]
        return rows[:limit]

    def reset(self) -> None:
        """Clear all state (also rewinds the tail offset and line buffer)."""
        with self._lock:
            self._entries.clear()
            self._by_ip.clear()
            self._offset = 1
            self._line_buf = ""

    # ---- session boundary (2026-10-24: stats restart on every vLLM (re)start) ----

    def mark_session_start(self) -> None:
        """A vLLM service (re)start was issued — restart the access stats
        for the fresh session. The previous instance's aggregates are
        dropped immediately (the panel shows no stale data before the next
        poll); the next well-formed poll skips the file's pre-restart tail
        and persists the session start point."""
        with self._lock:
            self._entries.clear()
            self._by_ip.clear()
            self._line_buf = ""
            self._skip_to_end = True

    def _resolve_session_file(self) -> None:
        if self._session_file is None:
            from .deployment_service import DATA_DIR
            self._session_file = os.path.join(DATA_DIR, SESSION_FILE_NAME)

    def _load_session_offset(self) -> None:
        """Resume from the persisted session start point when present —
        the pre-(re)start lines belong to the previous vLLM instance and
        are not part of the current session's stats. First run (no file)
        keeps the legacy full-read behavior (the offset stays 1)."""
        self._resolve_session_file()
        try:
            with open(self._session_file, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return
        offset = data.get("offset") if isinstance(data, dict) else None
        if isinstance(offset, int) and offset >= 0:
            self._offset = offset + 1
            logger.info(
                f"[VllmAccessLog] resuming from persisted session start "
                f"point (access log byte {offset})")

    def _persist_session_start(self, start: int) -> None:
        """Persist the session start point (access log byte size at the
        service (re)start). OSError-tolerant: a failed write only loses
        cross-dashboard-restart continuity; the in-memory session is
        unaffected."""
        self._resolve_session_file()
        try:
            os.makedirs(os.path.dirname(self._session_file), exist_ok=True)
            tmp = self._session_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"offset": start, "ts": time.time()}, f)
            os.replace(tmp, self._session_file)
        except OSError as e:
            logger.warning(
                f"[VllmAccessLog] session start point not persisted: {e}")

    def _invalidate_session_start(self) -> None:
        """Drop the persisted start point — the file rotated, so the
        recorded offset no longer references valid coordinates in the
        fresh file (a dashboard restart then reads the fresh file from
        the beginning, as before this feature)."""
        if self._session_file is None:
            return
        with contextlib.suppress(OSError):
            os.remove(self._session_file)

    # ---- ingest (parse access lines out of a raw log chunk) ----

    def ingest(self, text: str) -> None:
        """Parse complete access lines out of a raw log chunk.

        A trailing partial line is kept for the next chunk."""
        if not text:
            return
        with self._lock:
            parts = (self._line_buf + text).split("\n")
            lines, self._line_buf = parts[:-1], parts[-1]
            matched = []
            for line in lines:
                # Strip ANSI color codes first — the start wrapper forces
                # FORCE_COLOR, so a code sitting right before the IP would
                # break the `^|\s` boundary otherwise.
                m = ACCESS_LINE_RE.search(_ANSI_RE.sub("", line.rstrip("\r")))
                if not m:
                    continue
                path = m["path"].split("?", 1)[0]
                if path in _PROBE_PATHS:
                    continue
                matched.append((m["ip"], m["method"], path, int(m["status"])))
            if len(self._line_buf) > _MAX_LINE_BUF:
                self._line_buf = self._line_buf[-8192:]
        for ip, method, path, status in matched:
            self.record(ip, method, path, status)

    # ---- poller (tail the remote log file over SSH) ----

    def start_polling(self) -> None:
        # 2026-10-24: a dashboard (re)start must not re-read the whole file
        # — resume from the persisted vLLM session start point when present.
        self._load_session_offset()
        with self._lock:
            if self._poll_thread is not None and self._poll_thread.is_alive():
                return
            self._stop.clear()
            self._poll_thread = threading.Thread(
                target=self._loop, name="vllm-access-log", daemon=True)
            self._poll_thread.start()

    def stop_polling(self) -> None:
        self._stop.set()
        # Audit 2026-10-23 A-8: wait for the poller to actually exit — the
        # set()-only version could still be inside an in-flight SSH round
        # (up to 15 s) when shutdown returned, racing the next start or the
        # process exit.
        t = self._poll_thread
        if t is not None and t.is_alive():
            t.join(timeout=20)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.warning(f"[VllmAccessLog] poll failed: {e}")
            self._stop.wait(POLL_INTERVAL_SECONDS)

    def _build_poll_command(self, offset: int) -> str:
        # Marker line when the file shrank below the PREVIOUS read position
        # (the RotatingFileHandler rolled it over, or the file was deleted).
        # The threshold is offset-1, NOT offset (2026-10-24): after every
        # poll the offset sits one past the file end (offset = size+1), so
        # `S < offset` fired on every quiet poll where the file simply has
        # not grown — e.g. the minutes-long vLLM reload window — and the
        # reset re-aggregated the FULL historical file, defeating the
        # session-boundary stats. `; true` keeps the round a success even
        # when the file is missing so the (marker-only) output is consumed.
        # Audit 2026-10-23 A-4: the chunk is captured into $T ONCE and its
        # raw byte count reported (wc -c under LC_ALL=C — bytes, not
        # locale-dependent characters) on the VDB_BYTES line at the front of
        # the output, so the reader advances the offset by the bytes actually
        # read instead of re-encoding the decoded text. The raw `printf '%s'`
        # (no added trailing newline) keeps a mid-line chunk tail-able
        # across polls.
        # 2026-10-24: the VDB_SIZE line reports the file's TOTAL size — a
        # pending session start (mark_session_start) uses it to jump the
        # tail past the pre-restart data.
        return (
            f"S=$(stat -c %s {VLLM_ACCESS_LOG} 2>/dev/null || echo 0); "
            f'[ "$S" -lt {offset - 1} ] && echo {TRUNC_MARKER}; '
            f"echo {SIZE_MARKER}$"
            f"{{S}}; "
            f"T=$(LC_ALL=C tail -c +{offset} {VLLM_ACCESS_LOG} 2>/dev/null); "
            f"printf '{BYTES_MARKER}%s\\n' \"$(printf '%s' \"$T\" | LC_ALL=C wc -c)\"; "
            f"printf '%s' \"$T\"; true"
        )

    def _poll_once(self) -> None:
        from ..config.remote_client import config_manager
        from ..executor.remote_executor import get_remote_executor
        if not config_manager.get_config().is_remote():
            return
        with self._lock:
            offset = self._offset
        res = get_remote_executor().execute(
            self._build_poll_command(offset), timeout=15)
        if not res.get("success"):
            return
        self._consume_poll_output(res.get("stdout") or "")

    def _consume_poll_output(self, out: str) -> None:
        """Feed one poll's raw output (optional truncation marker, the
        VDB_SIZE / VDB_BYTES marker lines, and the tailed chunk)."""
        truncated = out.startswith(TRUNC_MARKER)
        if truncated:
            out = out[len(TRUNC_MARKER):].lstrip("\n")
            self.reset()
        m = _SIZE_RE.match(out)
        file_size: int | None = None
        if m:
            out = out[m.end():]
            file_size = int(m.group(1))
        m = _BYTES_RE.match(out)
        if m:
            out = out[m.end():]
            # Audit 2026-10-23 A-4: advance by the RAW byte count the remote
            # reported — the re-encoded text length is wrong whenever the
            # chunk contained invalid UTF-8 (U+FFFD: 1 byte -> 3), drifting
            # the offset forward and dropping the next poll's lines.
            byte_len = int(m.group(1))
        else:
            # No marker (malformed round / pre-A-4 executor output): fall
            # back to the old estimate — exact for valid-UTF-8 chunks.
            byte_len = len(out.encode("utf-8", errors="replace"))
        with self._lock:
            skip = self._skip_to_end
        if skip and file_size is not None:
            with self._lock:
                self._skip_to_end = False
            if truncated:
                # A rotation happened meanwhile: the fresh file's beginning
                # IS the session boundary (reset() already rewound the
                # offset to 1) — persist it; the fresh tail is counted.
                self._persist_session_start(0)
            else:
                # The tailed chunk still begins at the OLD offset — it is
                # the PRE-restart tail; drop it and jump the tail to the
                # file's current end so only fresh-session lines count.
                self._offset = file_size + 1
                self._persist_session_start(file_size)
                return
        elif skip:
            # Malformed round (no size marker): drop the stale chunk and
            # keep the flag pending WITHOUT advancing the offset — the next
            # poll re-tails from the same offset, so its VDB_SIZE still
            # reports the true file end and the skip then lands exactly.
            return
        if out:
            self.ingest(out)
        if byte_len:
            with self._lock:
                self._offset += byte_len
        if truncated and not skip:
            # A rotation without a pending session start invalidates the
            # persisted start point (it referenced the pre-rotation file).
            self._invalidate_session_start()


vllm_access_log = VllmAccessLog()
