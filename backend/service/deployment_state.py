"""Deployment persistent state (data/deployment_state.json).

Split out of ``deployment_service.py`` (file-size gate). Path constants
(STATE_FILE / DATA_DIR) live on the ``deployment_service`` facade and are
read through it at call time, so tests that patch the facade paths keep
working.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import suppress
from typing import Any

from . import deployment_service as _ds

logger = logging.getLogger(__name__)


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
        if not os.path.exists(_ds.STATE_FILE):
            return default
        try:
            with open(_ds.STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("top-level is not an object")
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning("deployment state unreadable (%s); using defaults", e)
            with suppress(OSError):
                os.replace(_ds.STATE_FILE, _ds.STATE_FILE + ".corrupt")
            return default
        merged = _merge_default(default, data)
        # History entries must be dicts (the UI reads .ts/.kind/.title/.status
        # off them); a malformed file must not crash module init.
        history = merged["history"]
        if not isinstance(history, list):
            merged["history"] = []
        else:
            merged["history"] = [h for h in history if isinstance(h, dict)][-100:]
        return merged

    def _save(self) -> None:
        os.makedirs(_ds.DATA_DIR, exist_ok=True)
        tmp = _ds.STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        if os.name == "posix":
            with suppress(OSError):
                os.chmod(tmp, 0o600)
        os.replace(tmp, _ds.STATE_FILE)

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
