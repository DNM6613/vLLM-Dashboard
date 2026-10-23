"""Per-IP note (备注) for the access panel (接入面板).

The connections table itself is an in-memory access log (cleared on
restart), but notes are user-managed and must survive redeploys — they
are kept in a small ip → note JSON file under the same data/ directory
(host-mounted volume) as the deployment state.
"""
from __future__ import annotations

import json
import os
import threading

from .deployment_service import DATA_DIR

NOTES_FILE = os.path.join(DATA_DIR, "connection_notes.json")
NOTE_MAX_LEN = 200


class ConnectionNotes:
    """Thread-safe ip → note map backed by a JSON file."""

    def __init__(self, path: str | None = None):
        self._path = path or NOTES_FILE
        self._lock = threading.Lock()
        self._notes = self._load()

    def _load(self) -> dict[str, str]:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items()
                if isinstance(k, str) and isinstance(v, str) and v}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._notes, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._notes)

    def set(self, ip: str, note: str) -> None:
        note = note.strip()
        if not note:
            self.clear(ip)
            return
        with self._lock:
            self._notes[ip] = note[:NOTE_MAX_LEN]
            self._save()

    def clear(self, ip: str) -> None:
        with self._lock:
            if ip in self._notes:
                del self._notes[ip]
                self._save()

    def reset(self, path: str | None = None) -> None:
        """Re-point (and reload) — used by tests to avoid the real data dir."""
        with self._lock:
            if path is not None:
                self._path = path
            self._notes = self._load()


connection_notes = ConnectionNotes()
