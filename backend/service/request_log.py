"""In-memory access log for /api/v1 (接入情况面板, Q2).

Records who is calling the API (client IP), how often (per-IP request
count), and what they sent (recent request entries with body previews).

In-memory by design: the dashboard is a monitoring console for a private
LAN, and the log restarts with the service — no disk persistence needed.
The ring buffer bounds memory (2000 entries × ~2KB body preview ≈ 4MB).
"""
import threading
import time
from collections import deque

MAX_ENTRIES = 2000
BODY_PREVIEW_CAP = 2048


class RequestLog:
    """Thread-safe ring of recent /api/v1 requests + per-IP aggregates."""

    def __init__(self, maxlen: int = MAX_ENTRIES):
        self._lock = threading.Lock()
        self._entries: deque[dict] = deque(maxlen=maxlen)
        self._by_ip: dict[str, dict] = {}

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
        """Clear all state (used by tests)."""
        with self._lock:
            self._entries.clear()
            self._by_ip.clear()


request_log = RequestLog()
