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
CONTAINER = os.environ.get("DEPLOY_CONTAINER", "vllm-dashboard-dev")
API_PORT = os.environ.get("DEPLOY_API_PORT", "5173")
API = f"http://127.0.0.1:{API_PORT}"
DATA_VOL = os.environ.get(
    "DEPLOY_DATA_VOL", f"/home/{USER}/vllm-dashboard-dev/data:/app/data")
BUILD_DIR = os.environ.get("DEPLOY_BUILD_DIR", f"/home/{USER}/vllm-dashboard-build")
TAR_LOCAL = "_server_deploy.tar"
TAR_REMOTE = os.environ.get("DEPLOY_TAR_REMOTE", f"/home/{USER}/_server_deploy.tar")

SHIP_FILES = ["Dockerfile", ".dockerignore", "requirements-lock.txt", "setup.py"]
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
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
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

def build_tar(path: str) -> None:
    with tarfile.open(path, "w:gz") as tf:
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
    fmt = "'{{.Names}} | {{.Image}} | {{.Status}}'"
    _, o, e = remote(
        c,
        "docker images vllm-dashboard 2>/dev/null | head -5; echo ---; "
        + f"docker ps -a --format {fmt} | grep {CONTAINER}; echo ---; "
        + f"wget -qO- {API}/health 2>&1 | head -c 300",
    )
    print(o, e)

def deploy(c: paramiko.SSHClient) -> None:
    tag = time.strftime("v%Y%m%d-%H%M%S")
    image = f"vllm-dashboard:{tag}"
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
    remote_with_rc(c, f"docker stop {CONTAINER} 2>/dev/null; "
                      f"docker rm {CONTAINER} 2>/dev/null; true")
    rc, o, e = remote(
        c,
        f"docker run -d --name {CONTAINER} --network host --restart always "
        f"-v {DATA_VOL} -e API_HOST=0.0.0.0 -e API_PORT={API_PORT} "
        f"-e STATIC_DIR=/app/static {image}",
        timeout=90,
    )
    print(o, e)
    if rc != 0:
        print(f"RUN FAILED (rc={rc})")
        sys.exit(1)

    print("[5/5] Health gate (up to 6 x 5s) ...")
    ok = False
    for i in range(6):
        time.sleep(5)
        rc, o, e = remote(c, f"wget -qO- {API}/health 2>&1")
        body = (o or "").strip()
        if "healthy" in body:
            ok = True
            print(f"      HEALTHY: {body[:300]}")
            break
        print(f"      attempt {i + 1}: {body[:160] or e.strip()[:160]}")

    if ok:
        remote(c, f"rm -rf {TAR_REMOTE} {BUILD_DIR}")
        print(f"DEPLOY_OK {image}")
    else:
        print("DEPLOY_FAILED (never became healthy)")
        sys.exit(1)

def verify(c: paramiko.SSHClient) -> None:
    ok = True

    print("=== [1/7] container ===")
    fmt = "'{{.Names}} | {{.Image}} | {{.Status}}'"
    _, o, _ = remote(c, f"docker ps --filter name={CONTAINER} --format {fmt}")
    print(o.strip() or "(empty)")

    print("=== [2/7] /health + version match ===")
    _, o, _ = remote(c, f"wget -qO- {API}/health")
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

    print("=== [3/7] openapi has shutdown endpoint ===")
    _, o, _ = remote(c, f"wget -qO- {API}/openapi.json 2>/dev/null | grep -c 'config/server/shutdown'")
    print(o.strip())
    if o.strip() == "0":
        ok = False
        print("  FAIL: endpoint missing")

    print("=== [4/7] bundle marker ===")
    _, o, _ = remote(c, f"docker exec {CONTAINER} sh -c "
                        "\"grep -rl 'Server Config' /app/static/assets/ 2>/dev/null | head -1\"")
    print(o.strip() or "(none)")
    if not o.strip():
        ok = False
        print("  FAIL: bundle marker missing")

    print("=== [5/7] model status (report only) ===")
    _, o, _ = remote(c, f"wget -qO- {API}/api/v1/models 2>/dev/null | head -c 400")
    print(o.strip() or "(empty)")

    print("=== [6/7] server status (report only) ===")
    _, o, _ = remote(c, f"wget -qO- {API}/api/v1/config/server/status 2>/dev/null | head -c 400")
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
    print("git push origin master ...")
    r = subprocess.run([git, "push", "origin", "master"])
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
