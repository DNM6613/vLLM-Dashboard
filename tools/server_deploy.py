from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

import paramiko

SERVER = os.environ.get("DEPLOY_TARGET", "")
USER = os.environ.get("DEPLOY_USER", "")
CONTAINER = os.environ.get("DEPLOY_CONTAINER", "vllm-dashboard")
API_PORT = os.environ.get("DEPLOY_API_PORT", "5174")
API = f"http://127.0.0.1:{API_PORT}"
DATA_VOL = os.environ.get(
    "DEPLOY_DATA_VOL", f"/home/{USER}/vllm-dashboard/data:/app/data")
BUILD_DIR = os.environ.get("DEPLOY_BUILD_DIR", f"/home/{USER}/vllm-dashboard-build")
TAR_LOCAL = "_server_deploy.tar"
TAR_REMOTE = os.environ.get("DEPLOY_TAR_REMOTE", f"/home/{USER}/_server_deploy.tar")
API_KEY = os.environ.get("DEPLOY_API_KEY", "")
# Audit 2026-10-23 C-2: the API key lives on the server as a 0600 file
# (``VLLM_DASHBOARD_API_KEY=<key>``) that docker reads via --env-file and
# the health probes read via $(sed ...) — the key itself never appears
# in a remote command line or the container's env.
KEY_FILE = os.environ.get("DEPLOY_KEY_FILE", f"/home/{USER}/.vdb_deploy_api_key")


def _key_header_arg() -> str:
    """The wget --header argument that resolves the API key FROM the 0600
    key file on the server (audit 2026-10-23 C-2). The command string is
    ps-visible for the whole probe window — only the (unexpanded) file
    reference may travel over SSH."""
    if not API_KEY:
        return ""
    return (f"--header=\"X-API-Key: "
            f"$(sed -n 's/^VLLM_DASHBOARD_API_KEY=//p' {KEY_FILE})\" ")

def _write_key_file(c: paramiko.SSHClient) -> None:
    """Overwrite the 0600 API-key file on the server (audit 2026-10-23
    C-2). The key travels the SFTP channel, never a command line, and the
    file is readable by the login user only. Idempotent — called from
    deploy, probe and verify so the probes always find a fresh key."""
    if not API_KEY:
        return
    sftp = c.open_sftp()
    try:
        with sftp.open(KEY_FILE, "w") as f:
            f.write(f"VLLM_DASHBOARD_API_KEY={API_KEY}\n")
        sftp.chmod(KEY_FILE, 0o600)
    finally:
        sftp.close()

def health_wget() -> str:
    # /health is behind the API-key gate when the container runs with a
    # key — the key itself is resolved on the server from the 0600 key
    # file (audit 2026-10-23 C-2), never inlined into the command.
    return f"wget -qO- {_key_header_arg()}{API}/health"

def _api_get_cmd(path: str) -> str:
    # Audit 2026-10-23 E-7: the report-only probes [5/7][6/7] must carry
    # the same key header as the health probes — with a key configured an
    # unauthenticated wget gets 401 and prints an empty body that looks
    # like "the API is down" during a manual verify.
    return f"wget -qO- {_key_header_arg()}{API}{path} 2>/dev/null | head -c 400"

# Audit 2026-10-23 E-8: process-level deploy mutex, held for the whole
# run. Two concurrent runs share the build dir / tar name / container,
# and a same-second tag let one run's rollback clobber the other's image;
# the lock makes the second process fail fast instead of corrupting
# state. The tag format stays vYYYYMMDD-HHMMSS — the lock already excludes
# the race a PID suffix would only paper over.
_deploy_lock_fh = None

def _acquire_deploy_lock() -> None:
    global _deploy_lock_fh
    lock_path = os.path.join(REPO_ROOT, ".deploy.lock")
    # The handle is held for the whole process run (global
    # _deploy_lock_fh); a `with` would release the mutex on return.
    fh = open(lock_path, "w")  # noqa: SIM115
    try:
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        print("ERROR: 另一个部署进程正在运行（.deploy.lock 被占用）——退出")
        sys.exit(1)
    _deploy_lock_fh = fh

SHIP_FILES = ["Dockerfile", ".dockerignore", "requirements-lock.txt"]
SHIP_DIRS = ["backend", "frontend"]
SKIP_DIRS = {
    "node_modules", "dist", "__pycache__", ".git", ".vscode", ".idea",
    "venv", "data", ".qwen", ".ruff_cache", ".pytest_cache",
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def local_version() -> str:
    path = os.path.join(REPO_ROOT, "backend", "__init__.py")
    with open(path, encoding="utf-8") as f:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
    return m.group(1) if m else "?"

def connect(password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    # Trusted internal dev server.
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507
    c.connect(SERVER, username=USER, password=password, timeout=15)
    return c

def remote(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    _, out, err = c.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return rc, out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")

def remote_with_rc(c: paramiko.SSHClient, cmd: str, timeout: int = 900) -> tuple[int, str, str]:
    rc, o, e = remote(c, cmd, timeout=timeout)
    if e.strip():
        print("[stderr]", e.strip()[-1500:])
    return rc, o, e

def _tracked_files() -> list[str] | None:
    """Tracked files under SHIP_FILES + SHIP_DIRS (git ls-files, i.e. the
    working-tree content including uncommitted edits). Audit 2026-10-22
    INF-6 — the filesystem walk used to ship untracked junk (build output,
    caches, editor files) into the tar. ``git archive HEAD`` is NOT used
    because the release flow deploys BEFORE committing, so archiving HEAD
    would drop the very changes being deployed. Returns None when git is
    unavailable (the caller falls back to the walk)."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        r = subprocess.run([git, "ls-files", "-z", "--",
                            *(SHIP_FILES + SHIP_DIRS)],
                           capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]


def _untracked_ship_files() -> list[str]:
    """Untracked (but not git-ignored) files under SHIP_FILES + SHIP_DIRS.
    Audit 2026-10-23 E-2: build_tar ships ONLY tracked files, so a NEW
    source file that was never ``git add``ed is silently missing from the
    image (incident 2026-10-23: a fresh backend module was absent and the
    container crashed at startup). Returns [] when git is unavailable."""
    git = shutil.which("git")
    if not git:
        return []
    try:
        r = subprocess.run([git, "ls-files", "--others", "--exclude-standard",
                            "-z", "--", *(SHIP_FILES + SHIP_DIRS)],
                           capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]


def build_tar(path: str) -> None:
    tracked = _tracked_files()
    if tracked is not None:
        # Audit 2026-10-23 E-2: new files never reach the image unless they
        # are tracked — surface them loudly instead of silently dropping
        # them (the release flow deploys before committing, so this is
        # expected to happen and must not block the deploy).
        untracked = _untracked_ship_files()
        for p in untracked:
            print(f"      WARNING: untracked file NOT shipped: {p}")
        if untracked:
            print("      -> git add the new files first or they will be "
                  "missing from the image")
    with tarfile.open(path, "w:gz") as tf:
        if tracked is not None:
            # Ship exactly the tracked files (worktree content) — the
            # Dockerfile / .dockerignore / requirements-lock.txt plus the
            # backend/ + frontend/ trees, with none of the untracked junk.
            for f in tracked:
                if os.path.isfile(f):
                    tf.add(f)
            return
        # Fallback (no git available): the original filesystem walk.
        for f in SHIP_FILES:
            if os.path.isfile(f):
                tf.add(f)
        for d in SHIP_DIRS:
            for root, dirs, files in os.walk(d):
                dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
                for name in files:
                    full = os.path.join(root, name)
                    tf.add(full, arcname=full)

def probe(c: paramiko.SSHClient) -> None:
    # Audit 2026-10-23 C-2: the health line resolves the key from the 0600
    # file — land a fresh one first (idempotent SFTP overwrite).
    _write_key_file(c)
    fmt = "'{{.Names}} | {{.Image}} | {{.Status}}'"
    _, o, e = remote(
        c,
        "docker images vllm-dashboard 2>/dev/null | head -5; echo ---; "
        + f"docker ps -a --format {fmt} | grep {CONTAINER}; echo ---; "
        + f"{health_wget()} 2>&1 | head -c 300",
    )
    print(o, e)

def run_container_cmd(image: str, api_key_arg: str) -> str:
    # Single source for the docker run args so the INF-2 rollback reruns
    # the previous image with the EXACT same parameters.
    # HF/pip mirrors are explicit opt-ins here (settings.py defaults are now
    # empty = official sources). The intranet dev host needs the mirrors.
    # Audit 2026-10-23 E-4: cap the container log (it was unbounded — the
    # rollback path reruns this same command, so it is covered too).
    return (
        f"docker run -d --name {CONTAINER} --network host --restart always "
        f"--log-opt max-size=50m --log-opt max-file=5 "
        f"-v {DATA_VOL} {api_key_arg}-e API_HOST=0.0.0.0 -e API_PORT={API_PORT} "
        f"-e STATIC_DIR=/app/static "
        f"-e HF_ENDPOINT=https://hf-mirror.com "
        f"-e PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ {image}"
    )

# Audit 2026-10-22 INF-11: the health-gate window is configurable via env —
# a large image on a slow disk can take longer than the fixed 6 x 5s to
# report healthy. Defaults preserve the original 6 x 5s behavior.
HEALTH_ATTEMPTS = int(os.environ.get("DEPLOY_HEALTH_ATTEMPTS", "6"))
HEALTH_INTERVAL = int(os.environ.get("DEPLOY_HEALTH_INTERVAL", "5"))


def health_gate(c: paramiko.SSHClient) -> bool:
    """Poll /health up to HEALTH_ATTEMPTS x HEALTH_INTERVAL s."""
    for i in range(HEALTH_ATTEMPTS):
        time.sleep(HEALTH_INTERVAL)
        _, o, e = remote(c, f"{health_wget()} 2>&1")
        body = (o or "").strip()
        if "healthy" in body:
            print(f"      HEALTHY: {body[:300]}")
            return True
        print(f"      attempt {i + 1}: {body[:160] or e.strip()[:160]}")
    return False


def _stop_and_remove(c: paramiko.SSHClient) -> None:
    """Stop + remove the running container, then VERIFY it is gone (audit
    2026-10-22 INF-11). The old ``2>/dev/null; true`` swallowed every
    failure — a container stuck in "removing" (busy / OOM-killing) would then
    leave a name conflict that the next ``docker run`` trips on. Now a
    non-empty ``docker ps -a`` result raises so the deploy stops instead of
    silently misbehaving."""
    remote(c, f"docker stop {CONTAINER} 2>/dev/null; "
              f"docker rm {CONTAINER} 2>/dev/null; true", timeout=120)
    fmt = "'{{.Names}}'"
    _, o, _ = remote(c, f"docker ps -a --filter 'name=^{CONTAINER}$' "
                        f"--format {fmt}")
    if o.strip():
        raise RuntimeError(
            f"container {CONTAINER} is still present after stop/rm "
            f"({o.strip()!r}) — aborting to avoid a name conflict on docker run")

def _roll_back(c: paramiko.SSHClient, old_image: str, api_key_arg: str) -> None:
    """Restart the previous image after the old container is already gone
    (audit 2026-10-22 INF-2 health-gate failure; 2026-10-23 E-1 run
    failure / SSH break): the only way back is the previous image with the
    exact same run parameters, re-verified by the health gate. The stop/rm
    first clears a half-started new container so its name cannot conflict.
    Every terminal path exits the script; on ROLLED_BACK the caller
    returns (production is back — this deployment is not)."""
    print(f"      rolling back to {old_image} ...")
    _stop_and_remove(c)
    rc, _, _ = remote(c, run_container_cmd(old_image, api_key_arg), timeout=90)
    if rc != 0:
        print(f"ROLLBACK RUN FAILED (rc={rc}) — start the previous image manually:")
        print(f"  {run_container_cmd(old_image, api_key_arg)}")
        sys.exit(1)
    print(f"      rollback health gate (up to {HEALTH_ATTEMPTS} x {HEALTH_INTERVAL}s) ...")
    if health_gate(c):
        print(f"ROLLED_BACK {old_image}")
        return
    print("ROLLBACK_FAILED — both new and previous image unhealthy")
    sys.exit(1)

def deploy(c: paramiko.SSHClient) -> None:
    tag = time.strftime("v%Y%m%d-%H%M%S")
    image = f"vllm-dashboard:{tag}"
    try:
        _run_deploy(c, image)
    finally:
        # Audit 2026-10-22 INF-10: remove the local tarball (it holds the
        # full source tree and leaked on every deploy) and the remote
        # upload/build artifacts on EVERY terminal path — success or
        # failure. The built docker image is intentionally kept (forensics
        # / rollback source).
        try:
            if os.path.exists(TAR_LOCAL):
                os.remove(TAR_LOCAL)
        except OSError as e:
            print(f"      WARNING: could not remove local {TAR_LOCAL}: {e}")
        try:
            remote(c, f"rm -rf {TAR_REMOTE} {BUILD_DIR}", timeout=60)
        except Exception as e:
            print(f"      WARNING: could not clean remote artifacts: {e}")


def _run_deploy(c: paramiko.SSHClient, image: str) -> None:
    print(f"[1/5] Building local tar {TAR_LOCAL} ...")
    build_tar(TAR_LOCAL)
    print(f"      tar size = {os.path.getsize(TAR_LOCAL) // 1024} KB")

    print("[2/5] SFTP upload ...")
    sftp = c.open_sftp()
    sftp.put(TAR_LOCAL, TAR_REMOTE)
    sftp.close()

    print("[3/5] Extract + docker build on server (this takes a few minutes) ...")
    remote_with_rc(c, f"rm -rf {BUILD_DIR} && mkdir -p {BUILD_DIR} && "
                      f"tar xzf {TAR_REMOTE} -C {BUILD_DIR}", timeout=180)
    rc, o, e = remote_with_rc(c, f"cd {BUILD_DIR} && docker build -t {image} .", timeout=1200)
    if rc != 0:
        print(f"BUILD FAILED (rc={rc})")
        sys.exit(1)
    print("      build tail:\n", o[-2000:])

    print("[4/5] stop/rm old container + run new ...")
    host_data_dir = DATA_VOL.split(":")[0]
    remote(c, f"mkdir -p {host_data_dir}")
    # Audit 2026-10-22 INF-2 (user ruling): remember the image currently
    # running so a failed health gate can roll back to it automatically.
    rc, o, e = remote(c, f"docker inspect --format '{{{{.Image}}}}' {CONTAINER} 2>/dev/null")
    old_image = (o or "").strip()
    if old_image:
        print(f"      previous image: {old_image}")
    # Audit 2026-10-22 INF-1: the container enforces auth when a key is
    # configured (settings.py reads VLLM_DASHBOARD_API_KEY). Without a key
    # the auth gate stays silently open.
    # Audit 2026-10-23 C-2: the key enters via a 0600 --env-file (landed
    # over SFTP) instead of -e — the docker run command line and
    # `docker inspect` must not carry the key.
    if API_KEY:
        _write_key_file(c)
    api_key_arg = f"--env-file {KEY_FILE} " if API_KEY else ""
    if not API_KEY:
        print("      WARNING: DEPLOY_API_KEY not set — the container will run WITHOUT an API key (auth open)")
    _stop_and_remove(c)
    try:
        rc, o, e = remote(c, run_container_cmd(image, api_key_arg), timeout=90)
    except Exception as ex:
        # Audit 2026-10-23 E-1: an SSH break mid-run leaves the old
        # container gone and the new one absent — treat it as a failed run
        # and roll back instead of leaving the port dark.
        print(f"RUN INTERRUPTED ({type(ex).__name__}: {ex})")
        rc, o, e = 1, "", ""
    print(o, e)
    if rc != 0:
        print(f"RUN FAILED (rc={rc})")
        if old_image:
            _roll_back(c, old_image, api_key_arg)
            return
        print("ROLLBACK_IMPOSSIBLE — no previous image recorded; start a known-good image manually:")
        print(f"  {run_container_cmd('<image>', api_key_arg)}")
        sys.exit(1)

    print(f"[5/5] Health gate (up to {HEALTH_ATTEMPTS} x {HEALTH_INTERVAL}s) ...")
    try:
        healthy = health_gate(c)
    except Exception as ex:
        # Audit 2026-10-23 E-1: an SSH break mid-gate leaves the new
        # container unverified — roll back rather than guess.
        print(f"HEALTH GATE INTERRUPTED ({type(ex).__name__}: {ex})")
        healthy = False
    if healthy:
        print(f"DEPLOY_OK {image}")
        return

    # Audit 2026-10-22 INF-2 (user ruling): the new image never became
    # healthy — roll back to the previous image with the same run
    # parameters and re-verify. The built docker image stays on the server
    # (forensics); the uploaded tar / build dir are cleaned by deploy().
    if old_image:
        print("      HEALTH GATE FAILED")
        _roll_back(c, old_image, api_key_arg)
        return

    print("DEPLOY_FAILED (never became healthy, no previous image to roll back to)")
    sys.exit(1)

def verify(c: paramiko.SSHClient) -> None:
    # Audit 2026-10-23 C-2: probes [2/7] and [3/7] resolve the key from the
    # 0600 file — land a fresh one first (idempotent SFTP overwrite).
    _write_key_file(c)
    ok = True

    print("=== [1/7] container ===")
    fmt = "'{{.Names}} | {{.Image}} | {{.Status}}'"
    _, o, _ = remote(c, f"docker ps --filter name={CONTAINER} --format {fmt}")
    print(o.strip() or "(empty)")

    print("=== [2/7] /health + version match ===")
    _, o, _ = remote(c, health_wget())
    body = o.strip()
    print(body[:300])
    if "healthy" not in body:
        ok = False
        print("  FAIL: not healthy")
    m = re.search(r'"version"\s*:\s*"([^"]+)"', body)
    prod_ver = m.group(1) if m else "?"
    local = local_version()
    print(f"  local={local} prod={prod_ver}")
    if prod_ver != local:
        ok = False
        print("  FAIL: version mismatch")

    print("=== [3/7] config-server API alive ===")
    # Audit 2026-10-22 SEC-3: /openapi.json is now closed (docs_url /
    # redoc_url / openapi_url = None in main.py), so a live-API check must use
    # a real business route. We probe GET /api/v1/config/server — NOT the
    # POST-only /server/shutdown: a probe (2026-10-22, prod) showed a GET on a
    # POST-only route returns 404, IDENTICAL to an absent path, because the
    # SPA static fallback (mounted after the API routers) swallows every
    # unmatched GET before FastAPI can answer 405. Presence of a POST route is
    # therefore NOT distinguishable via GET; a GET route in the same router
    # (200) is the reliable liveness signal. The probe carries the API key
    # when configured (resolved from the 0600 file — audit 2026-10-23
    # C-2): auth_middleware rejects every /api/v1 path BEFORE routing, so
    # an unauthenticated probe would see 401 for every route.
    header = _key_header_arg()
    _, o, _ = remote(c, f"wget -S -O /dev/null {header}{API}/api/v1/config/server 2>&1 "
                        f"| grep -oE 'HTTP/[0-9.]+ [0-9][0-9][0-9]' | head -1")
    line = o.strip()
    print(line or "(no status line — probe failed)")
    status = line.rsplit(" ", 1)[-1] if line else ""
    if status == "200":
        print("  OK: config-server route group is live")
    elif status == "401":
        ok = False
        print("  FAIL: 401 — API key mismatch (check DEPLOY_API_KEY)")
    else:
        ok = False
        print(f"  FAIL: unexpected status {status or '(none)'}")

    print("=== [4/7] bundle marker ===")
    _, o, _ = remote(c, f"docker exec {CONTAINER} sh -c "
                        "\"grep -rl 'Server Config' /app/static/assets/ 2>/dev/null | head -1\"")
    print(o.strip() or "(none)")
    if not o.strip():
        ok = False
        print("  FAIL: bundle marker missing")

    print("=== [5/7] model status (report only) ===")
    _, o, _ = remote(c, _api_get_cmd("/api/v1/models"))
    print(o.strip() or "(empty)")

    print("=== [6/7] server status (report only) ===")
    _, o, _ = remote(c, _api_get_cmd("/api/v1/config/server/status"))
    print(o.strip() or "(empty)")

    print("=== [7/7] log error count ===")
    _, o, _ = remote(c, f"docker logs {CONTAINER} 2>&1 | grep -icE 'traceback|error'")
    print(o.strip())

    print("VERIFY_OK" if ok else "VERIFY_FAILED")
    sys.exit(0 if ok else 1)

def push() -> None:
    git = shutil.which("git")
    if not git:
        print("PUSH_FAILED (git not found on PATH)")
        sys.exit(1)
    # Audit 2026-10-22 INF-7: Gitea is the default remote (user ruling
    # 2026-09-10); pushing to "origin" (GitHub) is on-request only.
    print("git push gitea master ...")
    r = subprocess.run([git, "push", "gitea", "master"])
    if r.returncode != 0:
        print(f"PUSH_FAILED (exit {r.returncode})")
        sys.exit(1)
    print("PUSH_OK")

def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    os.chdir(REPO_ROOT)
    if mode == "push":
        push()
        return
    if not SERVER:
        print("ERROR: DEPLOY_TARGET 环境变量未设置（set DEPLOY_TARGET=<服务器IP>& python ...）")
        sys.exit(1)
    if not USER:
        print("ERROR: DEPLOY_USER 环境变量未设置（set DEPLOY_USER=<用户名>& python ...）")
        sys.exit(1)
    password = os.environ.get("SERVER_PASS", "")
    if not password:
        print("ERROR: SERVER_PASS 环境变量未设置（set SERVER_PASS=<密码>& python ...）")
        sys.exit(1)
    _acquire_deploy_lock()
    c = connect(password)
    try:
        if mode == "probe":
            probe(c)
        elif mode == "deploy":
            deploy(c)
        elif mode == "verify":
            verify(c)
        else:
            print(__doc__)
            sys.exit(1)
    finally:
        c.close()

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
