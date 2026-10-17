"""Deployment pipelines — vLLM install, rollback, reboot, runtime compat.

Split out of ``deployment_service.py`` (file-size gate). Driver / CUDA
pipelines live in ``deployment_pipelines_env.py``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..executor.remote.deployment_ops import UV_PATH_PREFIX
from . import deployment_service as _ds
from .deployment_common import PY_VER_RE, DeploymentError
from .deployment_task_ctx import TaskContext

logger = logging.getLogger(__name__)


def _check_runtime_toolkit_compat(runtime_mode: str, cuda_selected: str,
                                  effective_cuda: str) -> None:
    """Interlock: the vLLM/torch CUDA family must match the effective system
    CUDA family. Cross-family mixes (a CUDA 12.9 toolkit with a CUDA 13
    torch backend, or a 13.x toolkit with the cu129 built-in wheel) fail at
    model startup. builtin = cu129 (CUDA 12.x); system = cu130/cu132 for a
    13.x selection, cu129 otherwise. When the system install is skipped, the
    effective toolkit is whatever the server already runs — manual installs
    included."""
    if not (cuda_selected or "").strip():
        # No CUDA version selected: plain vllm install — no torch family is
        # pinned, so there is nothing to interlock.
        return
    torch_family = "13" if (runtime_mode == "system"
                            and (cuda_selected or "").startswith("13")) else "12"
    eff = (effective_cuda or "").strip()
    eff_family = "13" if eff.startswith("13") else ("12" if eff.startswith("12") else "")
    if eff_family and eff_family != torch_family:
        if runtime_mode == "builtin":
            raise DeploymentError(
                "Built-in runtime is the CUDA 12.9 (cu129) wheel and does not "
                "match the CUDA 13.x system toolkit — use the system CUDA "
                "runtime (cu130/cu132) in the vLLM tab."
            )
        if torch_family == "13":
            raise DeploymentError(
                "System CUDA runtime installs a CUDA 13 torch backend "
                "(cu130/cu132) and does not match the CUDA 12.x system "
                "toolkit — use the built-in runtime (cu129) in the vLLM tab."
            )
        raise DeploymentError(
            "The selected CUDA 12.x version does not match the CUDA 13.x "
            "system toolkit — select a CUDA 13.x version in the CUDA tab."
        )


def _build_vllm_install_commands(payload: dict[str, Any], venv_path: str,
                                 mirrors: dict[str, Any]) -> tuple[str, str]:
    """Return (prepare_cmd, install_cmd)."""
    py_bin = f"{venv_path}/bin/python"
    mirror = (mirrors.get("pypi") or "").strip()
    index_args = f"--index-url {mirror}" if mirror else ""
    py_version = (payload.get("python_version") or "").strip()

    if payload.get("env_mode") == "new":
        py_args = f" --python {py_version}" if PY_VER_RE.fullmatch(py_version) else ""
        # Refuse to touch an existing venv: `uv venv` without --clear errors
        # out on one, and --clear would wipe a venv the running model may
        # live in (incident 2026-10-02: the fresh-install path stopped
        # vLLM, then died on the existing ~/.vllm). Fail fast with an
        # actionable hint instead.
        prepare = (
            f"test -d {venv_path} && {{ echo 'virtualenv already exists: "
            f"{venv_path}; choose the existing-environment mode or a "
            f"different venv name' >&2; exit 1; }}; "
            f"{UV_PATH_PREFIX}uv venv{py_args} {venv_path}"
        )
    else:
        prepare = (
            f"test -d {venv_path} && test -x {py_bin} || "
            f"{{ echo 'virtualenv not found: {venv_path}' >&2; exit 1; }}"
        )

    version = (payload.get("version") or "latest").strip()
    spec = "vllm" if version in ("", "latest") else f"vllm=={version}"

    # Every branch below invokes uv, so each carries UV_PATH_PREFIX — a
    # bare `uv` fails in the non-login SSH shell when uv lives in
    # ~/.local/bin (see deployment_ops.UV_PATH_PREFIX).
    cuda_version = (payload.get("cuda_version") or "").strip()
    if payload.get("source_build"):
        install = (
            UV_PATH_PREFIX + "git clone --depth 1 https://gh-proxy.com/https://github.com/vllm-project/vllm.git /tmp/vllm-src-vdb "
            + f"&& uv pip install {index_args} --python {py_bin} -e /tmp/vllm-src-vdb".replace("  ", " ")
        )
    elif not cuda_version:
        # No CUDA version selected: plain vllm install — PyPI's default torch
        # build applies, no torch index is pinned.
        install = UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} {spec}".replace("  ", " ")
    elif payload.get("runtime_mode") == "system":
        # Torch wheels ship per CUDA toolkit version. Verified 2026-09-18:
        # CUDA 13 has two channels — cu130 (torch 2.9.0+) and cu132 (torch
        # 2.12.0+); cu131/cu133 do not exist (S3 AccessDenied) and cu134
        # ships no torch wheels. 13.2 binds cu132; the other 13.x fall back
        # to cu130 (vLLM's docs standardize on it). 12.x follows the
        # selected version; 13.x follows the effective CUDA (the toolkit the
        # server actually runs), not the selection.
        eff = (payload.get("effective_cuda") or "").strip() or cuda_version
        if eff == "13.2":
            family = "cu132"
        elif eff.startswith("13"):
            family = "cu130"
        else:
            family = f"cu{cuda_version.replace('.', '')}"
        torch_index = f" --extra-index-url https://download.pytorch.org/whl/{family}"
        install = (
            UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} {spec} torch{torch_index}".replace("  ", " ")
        )
    else:
        # built-in runtime = explicit cu129 pin: the PyPI default torch build
        # is not named after its CUDA version, so pin the cu129 index to keep
        # the auto-bound family real instead of assumed.
        install = (
            UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} {spec} torch"
            " --extra-index-url https://download.pytorch.org/whl/cu129".replace("  ", " ")
        )

    return prepare, install


def _build_supervisor_script(venv_path: str, task_id: str) -> str:
    """Single-logical-line writer for the supervisor template.

    Same no-heredoc constraint as ``_build_cuda_env_script``: through the
    run_long wrapper + sudo() password-stdin function a heredoc writes a
    0-byte file (the old template survived the syntax error only because a
    valid ``if`` statement followed the terminator — the conf was still
    written empty).
    """
    conf = (
        "[program:vllm]",
        f"command={venv_path}/bin/vllm serve <MODEL_PATH> --port 8000",
        "autostart=false",
        "autorestart=false",
        "redirect_stderr=true",
        "stdout_logfile=/var/log/vllm/vllm.log",
    )
    tmp = f"/tmp/vdb_vllm_conf_{task_id}"
    return (
        "sudo mkdir -p /var/log/vllm; "
        "printf '%s\\n' "
        + " ".join(f"'{line}'" for line in conf)
        + f" > {tmp} && "
        f"sudo cp {tmp} /etc/supervisor/conf.d/vllm-vdb.conf && "
        f"sudo chmod 644 /etc/supervisor/conf.d/vllm-vdb.conf && "
        f"rm -f {tmp}; "
        "if command -v supervisorctl >/dev/null 2>&1; then "
        "sudo supervisorctl reread && sudo supervisorctl update; "
        "else echo 'supervisorctl not detected: template written only, configure manually'; fi"
    )


def _run_vllm(ctx: TaskContext, payload: dict[str, Any], venv_path: str,
              previous_version: str) -> None:
    mirrors = _ds.deployment_state.get()["mirrors"]
    prepare, install = _build_vllm_install_commands(payload, venv_path, mirrors)

    with ctx.step(0):
        stop = ctx.executor.stop_vllm(force=False)
        if stop.get("success"):
            ctx.log(f"vLLM stopped (mode: {stop.get('mode')})")
        else:
            ctx.log(f"warning: stop_vllm: {stop.get('error')}")
    with ctx.step(1):
        ctx.run_long(prepare, timeout=900)
    with ctx.step(2):
        ctx.run_long(install, timeout=7200)
    with ctx.step(3):
        verify = ctx.exec(
            f"bash -lc 'source {venv_path}/bin/activate 2>/dev/null; "
            f"vllm --version 2>&1 | head -n 3; python -c \"import vllm; print(vllm.__version__)\"'",
            timeout=120,
        )
        stdout = verify.get("stdout") or ""
        m = re.search(r"(\d+\.\d+\.\d+)", stdout)
        if not verify.get("success") or not m:
            raise DeploymentError(
                f"vLLM verification failed: {stdout.strip() or verify.get('stderr') or 'no output'}"
            )
        version = m.group(1)
        _ds.deployment_state.set_snapshot("vllm", version=version,
                                          previous_version=previous_version,
                                          venv=payload.get("venv_name", ""))
        ctx.log(f"vLLM {version} verified in {venv_path}.")

    if payload.get("auto_register_service") and len(ctx.task.steps) > 4:
        with ctx.step(4):
            sup_script = _build_supervisor_script(venv_path, ctx.task.id)
            ctx.run_long(sup_script, timeout=300)
            ctx.log("supervisor template written (autostart=false — edit the model path, then enable).")


def _run_reboot(ctx: TaskContext) -> None:
    with ctx.step(0):
        result = ctx.executor.reboot_server()
        if not result.get("success"):
            raise DeploymentError(result.get("error") or "Reboot command failed")
        ctx.log("Reboot command issued. The server should be back within 1-3 minutes.")


def _run_rollback_vllm(ctx: TaskContext, target_version: str, venv_path: str,
                       mirrors: dict[str, Any]) -> None:
    py_bin = f"{venv_path}/bin/python"
    mirror = (mirrors.get("pypi") or "").strip()
    index_args = f"--index-url {mirror}" if mirror else ""
    with ctx.step(0):
        stop = ctx.executor.stop_vllm(force=False)
        if stop.get("success"):
            ctx.log(f"vLLM stopped (mode: {stop.get('mode')})")
        else:
            ctx.log(f"warning: stop_vllm: {stop.get('error')}")
    with ctx.step(1):
        ctx.run_long((UV_PATH_PREFIX + f"uv pip install {index_args} --python {py_bin} vllm=={target_version}").replace("  ", " "),
                     timeout=7200)
    with ctx.step(2):
        verify = ctx.exec(
            f"bash -lc 'source {venv_path}/bin/activate 2>/dev/null; "
            f"python -c \"import vllm; print(vllm.__version__)\"'",
            timeout=120,
        )
        stdout = verify.get("stdout") or ""
        if not verify.get("success") or target_version not in stdout:
            raise DeploymentError(
                f"Rollback verification failed: {stdout.strip() or verify.get('stderr') or 'no output'}"
            )
        _ds.deployment_state.set_snapshot("vllm", version=target_version)
        ctx.log(f"vLLM rolled back to {target_version}.")
