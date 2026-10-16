"""Remote model operations: scan + download lifecycle (facade).

Split per the file-size gate (<=300 lines): scan helpers live in
``model_scan_ops.py`` (mixed in below); this module owns the download
lifecycle operations and re-exports the scan surface for backwards imports
(``from backend.executor.remote import model_ops``).
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

from ...config.server_config import has_parent_path_segment
from ...config.settings import settings
from .model_scan_ops import (
    _SCAN_EXCLUDE as _SCAN_EXCLUDE,
)
from .model_scan_ops import (
    HF_HUB_SCAN_ROOT as HF_HUB_SCAN_ROOT,
)
from .model_scan_ops import (
    ModelScanMixin as ModelScanMixin,
)
from .model_scan_ops import (
    _build_default_scan_command as _build_default_scan_command,
)
from .model_scan_ops import (
    _expand_scan_root as _expand_scan_root,
)

logger = logging.getLogger(__name__)

# Sentinel used to keep a `ps | grep` probe from matching its own command
# line (the pattern string itself would otherwise appear in the probe's args).
DL_PROBE_SENTINEL = "VDB_DL_PROBE_SENTINEL"
DL_PROCESS_PATTERN = r"hf download|huggingface-cli download"


class ModelOps(ModelScanMixin):

    def download_model(self, model_repo: str, model_save_path: str = "", hf_mirror: bool = True) -> dict[str, Any]:
        if model_save_path:
            if has_parent_path_segment(model_save_path):
                raise ValueError("Path traversal is not allowed")

            home_result = self.execute("echo $HOME")
            if not home_result["success"]:
                raise ValueError("Failed to resolve remote HOME directory")
            home_dir = home_result["stdout"].strip()
            if not home_dir:
                raise ValueError("Failed to resolve remote HOME directory")
            if model_save_path.startswith("~/"):
                model_save_path = home_dir + model_save_path[1:]
            elif model_save_path == "~":
                model_save_path = home_dir
            elif not model_save_path.startswith("/"):
                model_save_path = f"{home_dir}/{model_save_path}"
            if not model_save_path.startswith(home_dir + "/") and model_save_path != home_dir:
                raise ValueError("Model save path must be under home directory")

        log_file = f"/tmp/model_download_{model_repo.replace('/', '_')}.log"
        pid_file = f"{log_file}.pid"

        safe_repo = shlex.quote(model_repo)
        if model_save_path:
            download_cmd = f"hf download {safe_repo} --local-dir {shlex.quote(model_save_path)}"
        else:
            download_cmd = f"hf download {safe_repo}"

        activate_cmd = self._get_activate_cmd()

        env_prefix = ""
        if hf_mirror and settings.HF_ENDPOINT:
            env_prefix = (
                f"export HF_ENDPOINT={shlex.quote(settings.HF_ENDPOINT)} && "
                "export HF_HUB_DISABLE_XET=1 && "
            )
        # Gated repos reject anonymous downloads; inject the configured HF
        # token so `hf download` authenticates. Independent of the mirror —
        # the upstream endpoint needs it too.
        from ...config.remote_client import config_manager
        hf_token = (config_manager.get_config().hf_token or "").strip()
        if hf_token:
            env_prefix += f"export HF_TOKEN={shlex.quote(hf_token)} && "

        # The wrapper bash lives for the whole download (it execs `hf` as its
        # last command), so its PID is a reliable liveness marker. Written to
        # a pid file so status checks survive a backend restart and do not
        # depend on cmdline grep matching.
        cmd = (
            f"nohup bash -c \"{self._escape_double_quoted(env_prefix + activate_cmd + download_cmd)}\" "
            f"> {shlex.quote(log_file)} 2>&1 & P=$!; "
            f"echo $P > {shlex.quote(pid_file)}; echo $P"
        )

        result = self.execute(cmd)

        return {
            "success": result["success"] and result["stdout"].strip().isdigit(),
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "log_file": log_file
        }

    def get_download_total_size(self, model_repo: str, hf_mirror: bool = True) -> dict[str, Any]:
        """Total size in bytes of all files in the repo (HF API ``usedStorage``).

        Used as the denominator for byte-accurate download progress. Tries the
        endpoint the download itself uses first, then falls back to the other.
        Returns 0 when neither endpoint answers.
        """
        mirror = (settings.HF_ENDPOINT or "").rstrip("/")
        if hf_mirror:
            endpoints = [mirror, "https://huggingface.co"] if mirror else ["https://huggingface.co"]
        else:
            endpoints = ["https://huggingface.co", mirror] if mirror else ["https://huggingface.co"]
        # Gated repos can hide file metadata from anonymous API calls; send
        # the configured token as Bearer auth so usedStorage stays available.
        from ...config.remote_client import config_manager
        hf_token = (config_manager.get_config().hf_token or "").strip()
        auth_header = (
            f"-H {shlex.quote(f'Authorization: Bearer {hf_token}')} "
            if hf_token else ""
        )
        for endpoint in dict.fromkeys(endpoints):
            url = shlex.quote(f"{endpoint}/api/models/{model_repo}")
            cmd = (
                f"curl -sS --max-time 15 {auth_header}{url} "
                "| grep -o '\"usedStorage\":[0-9]*' | head -1 | cut -d: -f2"
            )
            result = self.execute(cmd)
            v = (result.get("stdout") or "").strip()
            if v.isdigit() and int(v) > 0:
                return {"success": True, "total_bytes": int(v)}
        return {"success": False, "total_bytes": 0}

    def get_download_size(self, model_repo: str, save_path: str = "") -> dict[str, Any]:
        """Bytes currently on disk for the download target.

        ``save_path`` uses the same semantics as ``download_model``: empty ->
        the HF cache dir for the repo, ``~``/``~/...``/relative -> under $HOME,
        absolute -> used as-is. Resolved in one shell command so it always
        matches where the download actually wrote.
        """
        repo_q = shlex.quote(model_repo)
        sp_q = shlex.quote(save_path)
        # Substring tests (not `~`-prefixed globs / `${SP#~/}`) because an
        # unquoted `~` in a shell pattern tilde-expands to $HOME, which breaks
        # both case matching and `#` stripping for `~/...` paths.
        script = (
            "REPO=" + repo_q + "; SP=" + sp_q + "; "
            "if [ -n \"$SP\" ]; then "
            "if [ \"${SP:0:1}\" = \"/\" ]; then T=\"$SP\"; "
            "elif [ \"$SP\" = \"~\" ]; then T=\"$HOME\"; "
            "elif [ \"${SP:0:2}\" = \"~/\" ]; then T=\"$HOME/${SP:2}\"; "
            "else T=\"$HOME/$SP\"; fi; "
            "else T=\"$HOME/.cache/huggingface/hub/models--${REPO//\\//--}\"; fi; "
            "DS=$(du -sb \"$T\" 2>/dev/null | awk '{print $1}'); "
            "echo \"DIRSIZE=${DS:-0}\""
        )
        result = self.execute(script)
        size = 0
        if result["success"]:
            for line in (result.get("stdout") or "").splitlines():
                if line.startswith("DIRSIZE="):
                    v = line[8:].strip()
                    if v.isdigit():
                        size = int(v)
                    break
        return {"success": result["success"], "size_bytes": size}

    def stop_download(self, model_repo: str) -> dict[str, Any]:
        """Terminate the running download for ``model_repo`` (if any)."""
        repo_q = shlex.quote(model_repo)
        script = (
            "PIDS=$(ps -eo pid=,args= 2>/dev/null "
            "| grep -v '" + DL_PROBE_SENTINEL + "' "
            "| grep -E '" + DL_PROBE_SENTINEL + "|" + DL_PROCESS_PATTERN + "' "
            "| grep -F " + repo_q + " "
            "| awk '{print $1}'); "
            "if [ -n \"$PIDS\" ]; then kill $PIDS 2>/dev/null && echo KILLED || echo KILL_FAIL; "
            "else echo NO_PROC; fi"
        )
        result = self.execute(script)
        out = (result.get("stdout") or "").strip()
        return {
            "success": result["success"],
            "killed": "KILLED" in out,
            "no_process": "NO_PROC" in out,
            "stderr": result.get("stderr", ""),
        }
