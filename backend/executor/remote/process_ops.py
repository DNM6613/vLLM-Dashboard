import secrets
import shlex
from typing import Any

_STOP_SELF_SENTINEL = "VDB_STOP_SENTINEL"

_VLLM_MAIN_PATTERN = r"vllm.entrypoints.openai.api_server|vllm serve"

def _ps_filter_self(pattern: str) -> str:
    return (
        f"ps -eo pid=,args= 2>/dev/null "
        f"| grep -v '{_STOP_SELF_SENTINEL}' "
        f"| grep -E '{_STOP_SELF_SENTINEL}|{pattern}' "
        f"| awk '{{print $1}}' "
        f"| tr '\\n' ' '"
    )

def _build_stop_command(model_path: str, signal_flag: str,
                        sentinel: str = _STOP_SELF_SENTINEL) -> str:
    if not model_path:
        return (
            f"{sentinel}=1; "
            f"global_pids=$({_ps_filter_self(_VLLM_MAIN_PATTERN)}); "
            f"for p in $global_pids; do kill {signal_flag} $p 2>/dev/null; pkill {signal_flag} -P $p 2>/dev/null; done; "
            "for gpid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do "
            "  gcmd=$(ps -p $gpid -o cmd= 2>/dev/null); "
            "  if echo \"$gcmd\" | grep -qi 'vllm'; then "
            f"    kill {signal_flag} $gpid 2>/dev/null; "
            "  fi; "
            "done; "
            f"tracker_pids=$({_ps_filter_self('multiprocessing.resource_tracker')}); "
            f"for p in $tracker_pids; do kill {signal_flag} $p 2>/dev/null; done; "
            "echo DONE"
        )

    mp_expr = shlex.quote(model_path)

    kill_main_and_children = (
        f"for p in $main_pids; do kill {signal_flag} $p 2>/dev/null; pkill {signal_flag} -P $p 2>/dev/null; done; "
    )

    gpu_cleanup_scoped = (
        "for gpid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do "
        "  gcmd=$(ps -p $gpid -o args= 2>/dev/null); "
        "  gppid=$(ps -o ppid= -p $gpid 2>/dev/null | tr -d ' '); "
        "  if echo \"$gcmd\" | grep -Fq \"$mp\"; then "
        f"    kill {signal_flag} $gpid 2>/dev/null; continue; "
        "  fi; "
        f"  for m in $main_pids; do if [ \"$gppid\" = \"$m\" ]; then kill {signal_flag} $gpid 2>/dev/null; fi; done; "
        "done; "
    )

    global_kill = (
        f"global_pids=$({_ps_filter_self(_VLLM_MAIN_PATTERN)}); "
        f"for p in $global_pids; do kill {signal_flag} $p 2>/dev/null; pkill {signal_flag} -P $p 2>/dev/null; done; "
        "for gpid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do "
        "  gcmd=$(ps -p $gpid -o cmd= 2>/dev/null); "
        "  if echo \"$gcmd\" | grep -qi 'vllm'; then "
        f"    kill {signal_flag} $gpid 2>/dev/null; "
        "  fi; "
        "done; "
        f"tracker_pids=$({_ps_filter_self('multiprocessing.resource_tracker')}); "
        f"for p in $tracker_pids; do kill {signal_flag} $p 2>/dev/null; done; "
    )

    return (
        f"{sentinel}=1; "
        f"mp={mp_expr}; "
        f"main_pids=$(ps -eo pid=,args= 2>/dev/null | grep -v '{sentinel}' | grep -F \"$mp\" | grep -i 'vllm' | awk '{{print $1}}' | tr '\\n' ' '); "
        "if [ -n \"$main_pids\" ]; then "
        f"{kill_main_and_children}"
        f"{gpu_cleanup_scoped}"
        "  echo SCOPED; "
        "else "
        f"{global_kill}"
        "  if [ -n \"$global_pids\" ]; then echo GLOBAL_FALLBACK; else echo NONE; fi; "
        "fi; "
        "echo DONE"
    )

class ProcessOps:

    def stop_vllm(self, force: bool = False, model_path: str = "") -> dict[str, Any]:
        signal_flag = "-9" if force else "-15"
        sentinel = f"{_STOP_SELF_SENTINEL}_{secrets.token_hex(8)}"
        cmd = _build_stop_command(model_path, signal_flag, sentinel=sentinel)
        result = self.execute(cmd)
        returncode = result.get("returncode", -1)
        stdout = result.get("stdout", "")

        if returncode != 0 and "DONE" not in stdout:
            return {
                "success": False,
                "error": result.get("stderr") or f"kill failed with return code {returncode}",
                "mode": "unknown",
            }

        if model_path:
            mode = "scoped" if "SCOPED" in stdout else ("global" if "GLOBAL_FALLBACK" in stdout else "none")
        else:
            mode = "global"
        return {"success": True, "error": None, "mode": mode}

    def shutdown_server(self) -> dict[str, Any]:
        if not self._is_remote():
            return {"success": False, "error": "Server shutdown requires remote SSH mode"}

        probe = self.execute("sudo -n -p '' true", timeout=10)
        if probe.get("success"):
            result = self.execute("sudo -n -p '' poweroff", timeout=20)
            if result.get("channel_open_failed"):
                return {
                    "success": False,
                    "error": result.get("stderr") or "Shutdown command could not be delivered to server",
                }
            return {"success": True, "error": None}

        if not self.password:
            return {
                "success": False,
                "error": (
                    probe.get("stderr")
                    or "sudo requires a password: set an SSH password or configure "
                       "NOPASSWD sudoers for passwordless poweroff"
                ),
            }

        pw = shlex.quote(self.password)
        probe = self.execute(f"echo {pw} | sudo -S -p '' true", timeout=10)
        if not probe.get("success"):
            return {
                "success": False,
                "error": probe.get("stderr") or "Cannot reach server or sudo authentication failed",
            }

        result = self.execute(f"echo {pw} | sudo -S -p '' poweroff", timeout=20)
        if result.get("channel_open_failed"):
            return {
                "success": False,
                "error": result.get("stderr") or "Shutdown command could not be delivered to server",
            }
        return {"success": True, "error": None}
