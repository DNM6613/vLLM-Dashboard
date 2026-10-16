"""Remote deployment probe commands and output parsers.

Split out of ``deployment_ops.py`` (file-size gate). The ``DeploymentOps``
executor-mixin class lives in ``deployment_ops.py``.
"""

from __future__ import annotations

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

# uv is normally installed to ~/.local/bin (standalone installer) or
# ~/.cargo/bin (cargo) — neither is on the PATH of the non-interactive,
# non-login shell that executor.execute() uses, so a bare `uv` fails with
# "command not found" even though uv is installed. Prepend the common
# install dirs to PATH for every command that invokes uv.
UV_PATH_PREFIX = 'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; '

# nvcc is usually NOT on the PATH of non-interactive shells (the toolkit
# install does not always ship a /etc/profile.d entry, and ~/.profile does
# not add /usr/local/cuda/bin), and `nvcc -V` reports "release X.Y" — not
# "CUDA Version X" — so resolve the binary from the standard install
# locations and match the real output format. Prints the version number or
# NO_NVCC.
CUDA_VERSION_PROBE = (
    'NVCC="$(command -v nvcc 2>/dev/null || true)"; '
    '[ -n "$NVCC" ] || for p in /usr/local/cuda/bin/nvcc /usr/local/cuda-*/bin/nvcc; do '
    '[ -x "$p" ] && NVCC="$p" && break; done; '
    'if [ -n "$NVCC" ]; then "$NVCC" -V 2>/dev/null | grep -oE "release [0-9]+[.][0-9]+" | head -n 1 | cut -d" " -f2; '
    'else echo NO_NVCC; fi'
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
        "echo '== CUDA =='; " + CUDA_VERSION_PROBE + "; "
        "echo '== PYTHON =='; python3 --version 2>&1 || echo NO_PYTHON; "
        "echo '== UV =='; " + UV_PATH_PREFIX + "uv --version 2>/dev/null || echo NO_UV; "
        "echo '== DISK =='; df -P / 2>/dev/null | awk 'NR==2 {print $4\"\\t\"$6}'; "
        "echo '== DRIVER_TOOL =='; which ubuntu-drivers 2>/dev/null || echo NO_UBUNTU_DRIVERS; "
        "echo '== NET =='; " + net_probe + "; "
        "echo '== END =='; echo DONE"
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
