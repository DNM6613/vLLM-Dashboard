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

import logging
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
# Shell marker emitted when the file shrank below the read offset (the
# RotatingFileHandler rolled it over to access.log.1) — aggregates restart
# with the fresh session.
TRUNC_MARKER = "VDB_LOG_TRUNC"

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

    def __init__(self, maxlen: int = MAX_ENTRIES):
        self._lock = threading.Lock()
        self._entries: deque[dict] = deque(maxlen=maxlen)
        self._by_ip: dict[str, dict] = {}
        # Next byte to read (1-based: `tail -c +N`); 1 = from the beginning.
        self._offset = 1
        # Partial line carried across poll chunks (a tail can split any line).
        self._line_buf = ""
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None

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
        with self._lock:
            if self._poll_thread is not None and self._poll_thread.is_alive():
                return
            self._stop.clear()
            self._poll_thread = threading.Thread(
                target=self._loop, name="vllm-access-log", daemon=True)
            self._poll_thread.start()

    def stop_polling(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.warning(f"[VllmAccessLog] poll failed: {e}")
            self._stop.wait(POLL_INTERVAL_SECONDS)

    def _build_poll_command(self, offset: int) -> str:
        # Marker line when the file shrank below the read offset (the
        # RotatingFileHandler rolled it over, or it was never created for a
        # manual launch); then tail from the offset. `; true` keeps the round
        # a success even when the file is missing so the (marker-only) output
        # is still consumed.
        return (
            f"S=$(stat -c %s {VLLM_ACCESS_LOG} 2>/dev/null || echo 0); "
            f'[ "$S" -lt {offset} ] && echo {TRUNC_MARKER}; '
            f"tail -c +{offset} {VLLM_ACCESS_LOG} 2>/dev/null; true"
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
        """Feed one poll's raw output (optional truncation marker + tail)."""
        truncated = out.startswith(TRUNC_MARKER)
        if truncated:
            out = out[len(TRUNC_MARKER):].lstrip("\n")
            self.reset()
        if out:
            self.ingest(out)
            with self._lock:
                self._offset += len(out.encode("utf-8", errors="replace"))


vllm_access_log = VllmAccessLog()
