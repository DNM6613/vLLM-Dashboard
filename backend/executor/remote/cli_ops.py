import logging
import re
import shlex
from typing import Any

from ...config.settings import settings

logger = logging.getLogger(__name__)

class CliOps:

    def check_cli_tools(self) -> dict[str, Any]:
        activate_cmd = self._get_activate_cmd()
        cmd = (
            f"{activate_cmd}"
            "hf_path=$(which hf 2>/dev/null); "
            "echo \"hf:$hf_path\""
        )
        result = self.execute(cmd)

        hf_installed = False
        if result["success"]:
            for line in result["stdout"].split("\n"):
                line = line.strip()
                if line.startswith("hf:") and line[3:].strip():
                    hf_installed = True

        return {
            "success": True,
            "hf_installed": hf_installed
        }

    def install_cli_tool(self, tool: str) -> dict[str, Any]:
        if tool != "hf":
            return {"success": False, "error": f"Unknown tool: {tool}"}

        activate_cmd = self._get_activate_cmd()
        index_args = f" --index-url {shlex.quote(settings.PIP_INDEX_URL)}" if settings.PIP_INDEX_URL else ""
        install_cmd = f"pip install{index_args} -U huggingface_hub"

        log_file = f"/tmp/cli_install_{tool}.log"
        cmd = f"nohup bash -c \"{self._escape_double_quoted(activate_cmd + install_cmd)}\" > {log_file} 2>&1 & echo $!"

        result = self.execute(cmd)
        return {
            "success": result["success"] and result["stdout"].strip().isdigit(),
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "log_file": log_file
        }

    def get_install_status(self, tool: str, log_file: str) -> dict[str, Any]:
        if tool != "hf":
            return {"status": "failed", "message": f"Unknown tool: {tool}"}

        if not re.fullmatch(r'/tmp/cli_install_hf\.log', log_file):
            return {"status": "failed", "message": "Invalid log file path"}

        safe_log = shlex.quote(log_file)

        result = self.execute(f"test -f {safe_log} && echo 'exists' || echo 'not found'")
        if "not found" in result["stdout"]:
            return {"status": "not_found", "message": "Install log not found"}

        _INSTALL_SENTINEL = "VDB_INSTALL_SENTINEL"
        install_cmd = (
            f"ps -eo args= 2>/dev/null "
            f"| grep -v '{_INSTALL_SENTINEL}' "
            f"| grep -E '{_INSTALL_SENTINEL}|pip install.*huggingface_hub' "
            f"| head -1"
        )
        result = self.execute(install_cmd)
        is_running = result["success"] and bool(result["stdout"].strip())

        if is_running:
            result = self.execute(f"tail -20 {safe_log}")
            return {
                "status": "installing",
                "log": result["stdout"] if result["success"] else "",
                "message": "Installing..."
            }

        activate_cmd = self._get_activate_cmd()
        verify_cmd = f"{activate_cmd}which {tool} 2>/dev/null"
        result = self.execute(verify_cmd)
        if result["success"] and result["stdout"].strip():
            return {"status": "complete", "message": f"{tool} installed successfully"}

        result = self.execute(f"tail -20 {safe_log}")
        return {
            "status": "failed",
            "log": result["stdout"] if result["success"] else "",
            "message": "Installation failed"
        }

    def ensure_hf_mirror(self) -> dict[str, Any]:
        return self.set_hf_mirror(True)

    def set_hf_mirror(self, enabled: bool) -> dict[str, Any]:
        BLOCK_START = "# >>> vllm-dashboard hf-mirror >>>"
        BLOCK_END = "# <<< vllm-dashboard hf-mirror <<<"

        if not enabled:
            check_cmd = (
                f"grep -q {shlex.quote(BLOCK_START)} ~/.bashrc 2>/dev/null "
                f"&& echo 'exists' || echo 'missing'"
            )
            check_result = self.execute(check_cmd)
            if not (check_result["success"] and "exists" in check_result["stdout"]):
                return {"success": True, "message": "hf-mirror block not in .bashrc", "configured": True}
            sed_pattern = f"/{BLOCK_START}/,/{BLOCK_END}/d"
            result = self.execute(f"sed -i {shlex.quote(sed_pattern)} ~/.bashrc")
            if not result["success"]:
                return {"success": False, "error": result.get("stderr", "Failed to update .bashrc"), "configured": False}
            return {"success": True, "message": "hf-mirror block removed from .bashrc", "configured": True}

        endpoint = settings.HF_ENDPOINT
        if not endpoint:
            return {"success": True, "message": "HF_ENDPOINT not set, skipping", "configured": True}

        safe_endpoint = shlex.quote(endpoint)
        block = (
            f"{BLOCK_START}\n"
            f"export HF_ENDPOINT={safe_endpoint}\n"
            f"export HF_HUB_DISABLE_XET=1\n"
            f"{BLOCK_END}"
        )

        check_cmd = (
            f"grep -q {shlex.quote(f'export HF_ENDPOINT={safe_endpoint}')} ~/.bashrc 2>/dev/null "
            f"&& grep -q {shlex.quote(BLOCK_START)} ~/.bashrc 2>/dev/null "
            f"&& echo 'exists' || echo 'missing'"
        )
        check_result = self.execute(check_cmd)
        if check_result["success"] and "exists" in check_result["stdout"]:
            return {"success": True, "message": "HF_ENDPOINT and HF_HUB_DISABLE_XET already in .bashrc", "configured": True}

        sed_pattern = f"/{BLOCK_START}/,/{BLOCK_END}/d"
        cmd = (
            f"sed -i {shlex.quote(sed_pattern)} ~/.bashrc && "
            f"printf '%s\\n' {shlex.quote(block)} >> ~/.bashrc"
        )
        result = self.execute(cmd)
        if not result["success"]:
            return {"success": False, "error": result.get("stderr", "Failed to update .bashrc"), "configured": False}

        return {"success": True, "message": "HF_ENDPOINT and HF_HUB_DISABLE_XET added to .bashrc (marker block)", "configured": True}
