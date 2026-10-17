"""Deployment pipelines — driver and CUDA toolkit installs.

Split out of ``deployment_service.py`` (file-size gate). vLLM install /
rollback pipelines live in ``deployment_pipelines_vllm.py``.
"""

from __future__ import annotations

import logging
import re

from ..executor.remote.deployment_ops import CUDA_VERSION_PROBE
from . import deployment_service as _ds
from .deployment_common import CUDA_MIN_DRIVER, DeploymentError
from .deployment_task_ctx import TaskContext

logger = logging.getLogger(__name__)


def check_cuda_compat(driver_major: int | None, cuda_version: str) -> str | None:
    required = CUDA_MIN_DRIVER.get(cuda_version)
    if required and driver_major is not None and driver_major < required:
        return (f"Current driver {driver_major} does not meet CUDA {cuda_version}'s "
                f"minimum requirement {required}; upgrade the driver first")
    return None


def _cuda_ver_key(ver: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in ver.split("."))
    except ValueError:
        return (0,)


def driver_max_cuda(driver_major: int | None) -> str:
    """Highest offered CUDA toolkit this driver can run — the largest
    version whose minimum driver is <= the driver major. 595 -> 13.2
    (verified 2026-09-18: nvidia-smi "CUDA Version: 13.2" on 595.91.07)."""
    if not driver_major:
        return ""
    best = ""
    for ver, need in CUDA_MIN_DRIVER.items():
        if need <= driver_major and _cuda_ver_key(ver) > _cuda_ver_key(best):
            best = ver
    return best


def _driver_pkg_major(package: str) -> int | None:
    m = re.match(r"nvidia-driver-(\d{3,4})", package or "")
    return int(m.group(1)) if m else None


def _run_driver(ctx: TaskContext, package: str, target_major: int | None) -> None:
    with ctx.step(0):
        stop = ctx.executor.stop_vllm(force=False)
        if stop.get("success"):
            ctx.log(f"vLLM stopped (mode: {stop.get('mode')})")
        else:
            ctx.log(f"warning: stop_vllm: {stop.get('error')}")
    with ctx.step(1):
        ctx.run_long(
            "sudo apt-get remove --purge -y 'nvidia-*' && sudo apt-get autoremove -y && sudo apt-get autoclean",
            timeout=1800,
        )
    with ctx.step(2):
        ctx.run_long(f"sudo apt-get update -y && sudo apt-get install -y {package}", timeout=3600)
        _ds.deployment_state.set_snapshot("driver", current_pkg=package)
    with ctx.manager._lock:
        ctx.task.status = "awaiting_reboot"
        if len(ctx.task.steps) > 3:
            ctx.task.steps[3]["status"] = "running"
        ctx.manager._persist_locked()
    ctx.log("Driver install complete. A server reboot is required for it to take effect; "
            "after the server is back this page verifies the new driver automatically.")


def _build_cuda_env_script(task_id: str) -> str:
    """Single-logical-line writer for /etc/profile.d/vllm-cuda.sh.

    Deliberately no heredoc: through the run_long wrapper a heredoc breaks
    two ways — the ``; ec=$?`` continuation lands on the line after the
    terminator (bash syntax error), and the sudo() password-stdin function
    leaves the child with an empty stdin, so tee writes a 0-byte file
    (incident 2026-10-02: both CUDA installs failed at this step).
    """
    lines = (
        "export CUDA_HOME=/usr/local/cuda",
        "export PATH=$CUDA_HOME/bin:$PATH",
        "export LD_LIBRARY_PATH=$CUDA_HOME/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}",
    )
    tmp = f"/tmp/vdb_cuda_env_{task_id}"
    return (
        "printf '%s\\n' "
        + " ".join(f"'{line}'" for line in lines)
        + f" > {tmp} && "
        f"sudo cp {tmp} /etc/profile.d/vllm-cuda.sh && "
        f"sudo chmod 644 /etc/profile.d/vllm-cuda.sh && "
        f"rm -f {tmp}"
    )


def _run_cuda(ctx: TaskContext, version: str, pkg: str) -> None:
    # Documented apt flow (NVIDIA CUDA installation guide): install the
    # signed cuda-keyring package, which registers the repo itself. Repo
    # directory is ubuntu<VERSION_ID without dot> (e.g. ubuntu2604); the repo
    # arch directory is x86_64/arm64 — note dpkg reports amd64, so map it.
    # The repo host geo-redirects (301) to a CDN (nvidia.cn in China), so
    # curl must follow redirects. The former ubuntu.pkgs.nvidia.com host no
    # longer resolves (verified on the AI server 2026-09-18).
    #
    # Keyring distro: the ubuntu2604 repo only carries the 13.3/13.4
    # toolkits (no 12.x, no 13.0–13.2), so on a 26.04 target the ubuntu2404
    # keyring is used for the rest — verified 2026-09-18: 12.9/13.0 install
    # cleanly on 26.04 from the ubuntu2404 repo. Other Ubuntu releases use
    # their own keyring (the ubuntu2404 repo carries 12.9–13.4).
    repo_script = (
        f"VER={version}; "
        "DISTRO=ubuntu$(. /etc/os-release && echo \"$VERSION_ID\" | tr -d '.'); "
        "ARCH=$(dpkg --print-architecture); [ \"$ARCH\" = amd64 ] && ARCH=x86_64; "
        "[ \"$DISTRO\" != ubuntu ] || { echo 'cannot detect Ubuntu version' >&2; exit 1; }; "
        "KEYD=$DISTRO; if [ \"$DISTRO\" = ubuntu2604 ]; then "
        "case \"$VER\" in 13.3|13.4) ;; *) KEYD=ubuntu2404;; esac; fi; "
        "sudo curl -fsSL -o /tmp/cuda-keyring.deb "
        "https://developer.download.nvidia.com/compute/cuda/repos/$KEYD/$ARCH/cuda-keyring_1.1-1_all.deb; "
        "sudo dpkg -i /tmp/cuda-keyring.deb && sudo rm -f /tmp/cuda-keyring.deb; "
        "sudo apt-get update -y"
    )
    with ctx.step(0):
        ctx.run_long(repo_script, timeout=900)
    with ctx.step(1):
        ctx.run_long(f"sudo apt-get install -y {pkg}", timeout=3600)
    with ctx.step(2):
        ctx.run_long(_build_cuda_env_script(ctx.task.id), timeout=120)
    with ctx.step(3):
        # CUDA_VERSION_PROBE prints the version nvcc actually reports
        # ("release X.Y") — the old check looked for "CUDA Version", which
        # nvcc -V never prints, so every install failed verification.
        verify = ctx.exec(CUDA_VERSION_PROBE, timeout=30)
        found = (verify.get("stdout") or "").strip()
        if not verify.get("success") or found in ("", "NO_NVCC"):
            raise DeploymentError(
                f"nvcc verification failed: {(verify.get('stdout') or verify.get('stderr')).strip()}"
            )
        _ds.deployment_state.set_snapshot("cuda", installed_version=found,
                                          toolkit_path="/usr/local/cuda")
    ctx.log(f"CUDA toolkit {found or version} installed and verified.")
