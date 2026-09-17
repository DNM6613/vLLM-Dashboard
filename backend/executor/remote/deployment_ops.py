"""Remote environment-deployment probes: GPU driver, CUDA, vLLM, conflicts.

Read-only / short operations only. Long-running mutation pipelines
(driver install, CUDA toolkit, vLLM) live in ``service/deployment_service.py``
and drive ``RemoteExecutor.execute`` step by step.
"""

import re
from typing import Any

# Attribute vocabulary emitted by `ubuntu-drivers devices`.
_DRIVER_ATTRS = ("distro", "third-party", "free", "non-free", "recommended")


_TAG_COLORS = {
    "version": "blue",
    "server": "orange",
    "open": "green",
    "distro": "purple",
    "third-party": "purple",
    "free": "pink",
    "non-free": "pink",
    "recommended": "yellow",
}

_VERSION_RE = re.compile(r"-driver-(\d{3,4})")

def _driver_tags(package: str, attrs: str) -> list[dict[str, str]]:
    """Build the coloured tag list for one driver package line."""
    tags: list[dict[str, str]] = []
    vmatch = _VERSION_RE.search(package)
    if vmatch:
        tags.append({"key": "version", "label": vmatch.group(1), "color": _TAG_COLORS["version"]})
    if "-server" in package:
        tags.append({"key": "server", "label": "server", "color": _TAG_COLORS["server"]})
    if package.endswith("-open"):
        tags.append({"key": "open", "label": "open", "color": _TAG_COLORS["open"]})
    for attr in attrs.split():
        if attr in _DRIVER_ATTRS:
            tags.append({"key": attr, "label": attr, "color": _TAG_COLORS[attr]})
    return tags

def parse_ubuntu_drivers(raw: str) -> dict[str, Any]:
    """Parse `ubuntu-drivers devices` output into GPU models + driver rows.

    Rules:
    - drop log noise (`ERROR:root:...`, `== /sys/... ==` block headers,
      `modalias :`, `vendor :` lines)
    - capture `model : ...` lines as GPU model names (card title)
    - strip the `driver   : ` prefix; split `name - attrs` on ` - `
    - dedupe by package name (multi-GPU machines list the same driver per GPU)
    """
    gpu_models: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("=="):
            continue
        lowered = line.lower()
        if lowered.startswith("modalias") or lowered.startswith("vendor"):
            continue
        if lowered.startswith("model"):
            _, sep, value = line.partition(":")
            if sep:
                model_name = value.strip()
                if model_name and model_name not in gpu_models:
                    gpu_models.append(model_name)
            continue
        if "ERROR" in line.upper() and "driver" not in lowered:
            continue
        if not lowered.startswith("driver"):
            continue
        _, sep, payload = line.partition(":")
        if not sep:
            continue
        payload = payload.strip()
        name_part, sep_attrs, attrs_part = payload.partition(" - ")
        package = name_part.strip()
        if not package or package in seen:
            continue
        seen.add(package)
        attrs = attrs_part.strip() if sep_attrs else ""
        vmatch = _VERSION_RE.search(package)
        rows.append({
            "package": package,
            "version": int(vmatch.group(1)) if vmatch else None,
            "attrs": [a for a in attrs.split() if a in _DRIVER_ATTRS],
            "tags": _driver_tags(package, attrs),
            "recommended": "recommended" in attrs,
            "nouveau": "nouveau" in package,
        })

    return {"gpu_models": gpu_models, "drivers": rows}

# One combined probe: driver list + current driver version (nvidia-smi) +
# apt-installed nvidia-driver-* package names with their dpkg status.
# dpkg-query -W lists packages of ANY status-db state (including `un`,
# never-installed entries), so the plain `${Status}` column is required and
# the parser keeps only `install ok installed` (ii) rows. This lets the UI
# mark the exact installed variant (e.g. -server) instead of every package
# that shares the major version number.
DRIVER_LIST_CMD = (
    "ubuntu-drivers devices 2>/dev/null; "
    "echo '---VDB_SEP---'; "
    "nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1; "
    "echo '---VDB_SEP2---'; "
    "dpkg-query -W -f='${Package}\\t${Status}\\n' 'nvidia-driver-*' 2>/dev/null; "
    "echo DONE"
)

# Network endpoints probed by the preflight (PyPI / mirrors / HF / ModelScope).
NET_PROBES = (
    "https://pypi.org/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://hf-mirror.com/",
    "https://modelscope.cn/",
)

def build_preflight_command() -> str:
    """One SSH round-trip: OS / kernel / GPU / CUDA / Python / uv / disk /
    ubuntu-drivers / network, each section bracketed by `== NAME ==` markers
    so :func:`split_sections` can split the output."""
    net_probe = (
        "for u in " + " ".join(f'"{u}"' for u in NET_PROBES) + "; do "
        "code=$(curl -sI --max-time 5 -o /dev/null -w '%{http_code}' \"$u\" 2>/dev/null); "
        "echo \"NET $u ${code:-TIMEOUT}\"; done"
    )
    return (
        "echo '== OS =='; (lsb_release -ds 2>/dev/null || grep PRETTY /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"') ; "
        "echo '== KERNEL =='; uname -r; "
        "echo '== GPU =='; nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader 2>/dev/null || echo NO_NVIDIA_SMI; "
        "echo '== CUDA =='; bash -lc 'nvcc -V 2>/dev/null | grep -o \"CUDA Version [0-9.]*\" || echo NO_NVCC'; "
        "echo '== PYTHON =='; python3 --version 2>&1 || echo NO_PYTHON; "
        "echo '== UV =='; uv --version 2>&1 || echo NO_UV; "
        "echo '== DISK =='; df -P / 2>/dev/null | awk 'NR==2 {print $4\"\\t\"$6}'; "
        "echo '== DRIVER_TOOL =='; which ubuntu-drivers 2>/dev/null || echo NO_UBUNTU_DRIVERS; "
        "echo '== NET =='; " + net_probe + "; "
        "echo '== END =='; echo DONE"
    )

CONFLICT_SCAN_CMD = (
    "dpkg -l 2>/dev/null | awk '$1 ~ /^(ii|rc|un)/ && "
    r'$2 ~ /nvidia|cuda|cublas|cudnn|nccl|tensorrt/ {print $1 "\t" $2 "\t" $3}'
    " | head -n 100; echo DONE"
)

def split_sections(output: str) -> dict[str, str]:
    """Split `== NAME ==` bracketed probe output into {name: body}."""
    sections: dict[str, str] = {}
    current = ""
    lines: list[str] = []
    for line in output.splitlines():
        m = re.match(r"^== ([A-Z0-9_]+) ==\s*$", line.strip())
        if m:
            if current:
                sections[current] = "\n".join(lines).strip()
            current = m.group(1)
            lines = []
            continue
        if current:
            lines.append(line)
    if current:
        sections[current] = "\n".join(lines).strip()
    return sections

def parse_net_lines(sections: dict[str, str]) -> dict[str, str]:
    """`NET <url> <code|TIMEOUT>` lines -> {url: code}."""
    net: dict[str, str] = {}
    for line in (sections.get("NET", "") or "").splitlines():
        parts = line.strip().split()
        if len(parts) == 3 and parts[0] == "NET":
            net[parts[1]] = parts[2]
    return net

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
        return {
            "success": True,
            "os": sections.get("OS", ""),
            "kernel": sections.get("KERNEL", ""),
            "gpus": gpus,
            "gpu_missing": "NO_NVIDIA_SMI" in (sections.get("GPU", "") or ""),
            "cuda": (sections.get("CUDA", "") or "").replace("CUDA Version", "").strip(),
            "python": (sections.get("PYTHON", "") or "").replace("Python", "").strip(),
            "uv": (sections.get("UV", "") or "").replace("uv ", "").strip(),
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
            "echo '== UV =='; uv --version 2>&1 || echo NO_UV; "
            "echo '== PYTHONS =='; uv python list --only-installed 2>/dev/null | head -n 20; "
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

        return {
            "success": True,
            "uv": (sections.get("UV", "") or "").replace("uv ", "").strip(),
            "uv_missing": "NO_UV" in (sections.get("UV", "") or ""),
            "pythons": pythons,
            "venvs": venvs,
            "vllm_version": vllm_version,
        }

    def scan_conflicts(self) -> dict[str, Any]:
        result = self.execute(CONFLICT_SCAN_CMD, timeout=30)
        if not result["success"]:
            return {"success": False, "error": result.get("stderr") or "conflict scan failed", "packages": []}
        packages: list[dict[str, str]] = []
        for line in (result.get("stdout", "") or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0] in ("ii", "rc", "un"):
                packages.append({"state": parts[0], "name": parts[1], "version": parts[2]})
        return {"success": True, "packages": packages}

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
