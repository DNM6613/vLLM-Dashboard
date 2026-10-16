"""Remote environment-deployment probes: GPU driver, CUDA, vLLM, conflicts.

Read-only / short operations only. Long-running mutation pipelines
(driver install, CUDA toolkit, vLLM) live in ``service/deployment_*.py``
and drive ``RemoteExecutor.execute`` step by step.

Split per the file-size gate (<=300 lines): the probe commands and output
parsers live in ``deployment_commands.py``; this module owns the
``DeploymentOps`` executor mixin and re-exports the command/parser surface
for backwards imports (``from ..executor.remote import deployment_ops``).
"""

from __future__ import annotations

import re
from typing import Any

from .deployment_commands import (
    _DRIVER_ATTRS as _DRIVER_ATTRS,
)
from .deployment_commands import (
    _TAG_COLORS as _TAG_COLORS,
)
from .deployment_commands import (
    _VERSION_RE as _VERSION_RE,
)
from .deployment_commands import (
    CUDA_VERSION_PROBE as CUDA_VERSION_PROBE,
)
from .deployment_commands import (
    DRIVER_LIST_CMD as DRIVER_LIST_CMD,
)
from .deployment_commands import (
    NET_PROBES as NET_PROBES,
)
from .deployment_commands import (
    UV_PATH_PREFIX as UV_PATH_PREFIX,
)
from .deployment_commands import (
    _driver_tags as _driver_tags,
)
from .deployment_commands import (
    build_preflight_command as build_preflight_command,
)
from .deployment_commands import (
    parse_net_lines as parse_net_lines,
)
from .deployment_commands import (
    parse_ubuntu_drivers as parse_ubuntu_drivers,
)
from .deployment_commands import (
    split_sections as split_sections,
)


class DeploymentOps:

    def get_driver_list(self) -> dict[str, Any]:
        result = self.execute(DRIVER_LIST_CMD, timeout=30)
        if not result["success"]:
            return {"success": False, "error": result.get("stderr") or "ubuntu-drivers probe failed",
                    "gpu_models": [], "drivers": [], "current_driver": ""}
        stdout = result.get("stdout", "")
        sep_index = stdout.find("---VDB_SEP---")
        sep2_index = stdout.find("---VDB_SEP2---")
        drivers_raw = stdout[:sep_index] if sep_index >= 0 else stdout
        if sep_index < 0:
            between = ""
        elif sep2_index >= 0:
            between = stdout[sep_index + len("---VDB_SEP---"): sep2_index]
        else:
            # legacy probe without the dpkg section: everything after the
            # first separator is the version block
            between = stdout[sep_index + len("---VDB_SEP---"):]
        after_sep = stdout[sep2_index + len("---VDB_SEP2---"):] if sep2_index >= 0 else ""
        parsed = parse_ubuntu_drivers(drivers_raw)
        current = ""
        for line in between.splitlines():
            line = line.strip()
            if line and line != "DONE" and re.match(r"^\d{3,4}\.\d+(?:\.\d+)?$", line):
                current = line
                break
        installed_packages: list[str] = []
        for line in after_sep.splitlines():
            name, _, status = line.partition("\t")
            # only genuinely installed (ii) packages — dpkg-query -W also
            # lists never-installed (un) and removed (rc) status-db entries
            if "installed" not in status.split():
                continue
            name = name.strip()
            if re.match(r"^nvidia-driver-\d{3,4}(-[a-z0-9]+)*$", name):
                installed_packages.append(name)
        parsed["current_driver"] = current
        parsed["installed_packages"] = installed_packages
        parsed["success"] = True
        return parsed

    def get_preflight(self) -> dict[str, Any]:
        result = self.execute(build_preflight_command(), timeout=60)
        if not result["success"]:
            return {"success": False, "error": result.get("stderr") or "preflight failed"}
        sections = split_sections(result.get("stdout", ""))
        net = parse_net_lines(sections)
        gpus: list[dict[str, str]] = []
        for line in (sections.get("GPU", "") or "").splitlines():
            if "," in line:
                parts = [p.strip() for p in line.split(",", 3)]
                if len(parts) >= 2 and parts[0].isdigit():
                    gpus.append({
                        "index": parts[0],
                        "name": parts[1] if len(parts) > 1 else "",
                        "driver": parts[2] if len(parts) > 2 else "",
                        "memory": parts[3] if len(parts) > 3 else "",
                    })
        # Sentinel values (NO_NVCC / NO_UV) must never leak to the UI: the
        # backend always emits a stable, translatable English string.
        # CUDA_VERSION_PROBE already prints the bare version number.
        cuda_raw = (sections.get("CUDA", "") or "").strip()
        if not cuda_raw or cuda_raw == "NO_NVCC":
            cuda_raw = "not installed (optional — vLLM ships its own runtime)"

        # A "command not found" line means uv is genuinely missing (the probe
        # already extends PATH); extract the version number when present.
        uv_raw = (sections.get("UV", "") or "").strip()
        m = re.search(r"(\d+\.\d+\.\d+)", uv_raw)
        uv_version = m.group(1) if m else ""

        return {
            "success": True,
            "os": sections.get("OS", ""),
            "kernel": sections.get("KERNEL", ""),
            "gpus": gpus,
            "gpu_missing": "NO_NVIDIA_SMI" in (sections.get("GPU", "") or ""),
            "cuda": cuda_raw,
            "python": (sections.get("PYTHON", "") or "").replace("Python", "").strip(),
            "uv": uv_version,
            "uv_missing": not uv_version,
            "disk": (sections.get("DISK", "") or "").split("\t"),
            "driver_tool": (sections.get("DRIVER_TOOL", "") or "").strip() != "NO_UBUNTU_DRIVERS",
            "net": net,
        }

    def get_vllm_env(self) -> dict[str, Any]:
        """uv / python interpreters / existing venvs / current vLLM version.

        The vllm probe sources the configured venv (``_get_activate_cmd``)
        so the version reflects that environment.
        """
        activate_cmd = self._get_activate_cmd()
        vllm_probe = f"{activate_cmd}(vllm --version 2>/dev/null || echo NO_VLLM)"
        cmd = (
            f"echo '== UV =='; {UV_PATH_PREFIX}uv --version 2>/dev/null || echo NO_UV; "
            f"echo '== PYTHONS =='; {UV_PATH_PREFIX}uv python list --only-installed 2>/dev/null | head -n 20; "
            "echo '== VENVS =='; find $HOME -maxdepth 3 -name pyvenv.cfg -not -path '*/node_modules/*' 2>/dev/null | head -n 20; "
            f"echo '== VLLM =='; {vllm_probe}; "
            "echo '== END =='; echo DONE"
        )
        result = self.execute(cmd, timeout=30)
        if not result["success"]:
            return {"success": False, "error": result.get("stderr") or "vllm env probe failed"}
        sections = split_sections(result.get("stdout", ""))

        pythons: list[str] = []
        for line in (sections.get("PYTHONS", "") or "").splitlines():
            m = re.match(r"^\s*(\d+\.\d+(?:\.\d+)?)\s", line)
            if m:
                v = m.group(1)
                if v not in pythons:
                    pythons.append(v)

        venvs: list[str] = []
        for line in (sections.get("VENVS", "") or "").splitlines():
            line = line.strip()
            if line.endswith("pyvenv.cfg"):
                path = line[: -len("pyvenv.cfg")].rstrip("/")
                if path and path not in venvs:
                    venvs.append(path)

        vllm_raw = (sections.get("VLLM", "") or "").strip()
        vllm_version = ""
        if vllm_raw and "NO_VLLM" not in vllm_raw:
            for line in vllm_raw.splitlines():
                m = re.search(r"(\d+\.\d+\.\d+)", line)
                if m:
                    vllm_version = m.group(1)
                    break

        # Same sentinel discipline as get_preflight: never surface raw
        # "command not found" / NO_UV markers, only a version or missing.
        uv_raw = (sections.get("UV", "") or "").strip()
        m = re.search(r"(\d+\.\d+\.\d+)", uv_raw)
        uv_version = m.group(1) if m else ""

        return {
            "success": True,
            "uv": uv_version,
            "uv_missing": not uv_version,
            "pythons": pythons,
            "venvs": venvs,
            "vllm_version": vllm_version,
        }

    def reboot_server(self) -> dict[str, Any]:
        """Issue `sudo reboot` the same way shutdown issues poweroff."""
        if not self._is_remote():
            return {"success": False, "error": "Server reboot requires remote SSH mode"}

        probe = self.execute("sudo -n -p '' true", timeout=10)
        if probe.get("success"):
            result = self.execute("sudo -n -p '' reboot", timeout=15)
            return {"success": not result.get("channel_open_failed"),
                    "error": result.get("stderr") if result.get("channel_open_failed") else None}

        if not self.password:
            return {
                "success": False,
                "error": (
                    probe.get("stderr")
                    or "sudo requires a password: set an SSH password or configure "
                       "NOPASSWD sudoers for passwordless reboot"
                ),
            }

        pw_input = self.password + "\n"
        probe = self.execute("sudo -S -p '' true", timeout=10, stdin_data=pw_input)
        if not probe.get("success"):
            return {
                "success": False,
                "error": probe.get("stderr") or "sudo authentication failed",
            }

        # Reboot kills the channel mid-command: anything other than a
        # channel-open failure means the command was delivered.
        result = self.execute("sudo -S -p '' reboot", timeout=15, stdin_data=pw_input)
        return {"success": not result.get("channel_open_failed"),
                "error": result.get("stderr") if result.get("channel_open_failed") else None}

    def list_installed_driver_packages(self) -> dict[str, Any]:
        """`dpkg -l` for installed nvidia-driver packages (rollback snapshot)."""
        result = self.execute(
            "dpkg -l 2>/dev/null | awk '$1 == \"ii\" && $2 ~ /^nvidia-driver-/ {print $2\"\\t\"$3}' "
            "| head -n 20; echo DONE",
            timeout=20,
        )
        packages: list[dict[str, str]] = []
        if result["success"]:
            for line in (result.get("stdout", "") or "").splitlines():
                parts = line.split("\t")
                if len(parts) == 2:
                    packages.append({"name": parts[0], "version": parts[1]})
        return {"success": result["success"], "packages": packages}
