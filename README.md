# vLLM-Dashboard

[中文](README-CN.md)

A management panel for vLLM inference servers: monitor model and server hardware status remotely in real time through a browser. Configure environment variables and model launch parameters with ease, with an integrated SSH-based console for server configuration management.

## Features

- **Model management**: view loaded models, start/stop inference services with one click, configure launch parameters
- **Real-time monitoring**: running/queued request counts; hardware metrics (GPU, CPU, memory) refreshed in real time
- **Model download**: pull models from Hugging Face with visible progress
- **Benchmarking**: built-in performance test
- **Web terminal**: built-in SSH console, no extra client needed
- **Power management**: remote power on/off and power status via BMC/IPMI; without BMC/IPMI, remote shutdown via SSH only
- **Server configuration**: unified management of the target server's SSH, inference API, and BMC credentials
- **Access protection**: API-key gate; Chinese/English UI and light/dark themes

## Scope

- Self-hosted single LLM inference server, e.g. debugging and operations of AI servers in labs / data centers

## Environment

- **Deployment environment**: Linux / Windows, Docker or Python 3.10+
- **Managed target**: Linux server with SSH enabled, running the vLLM engine on NVIDIA GPU + CUDA (other GPU brands not supported yet — no test platform available)
- **Browser**: Chrome / Edge / Firefox and other modern browsers
- **API key (optional)**: set `VLLM_DASHBOARD_API_KEY` (or `API_KEY`) in `.env` to gate every API behind the `X-API-Key` header — the UI prompts for the key on first visit. Leave it empty (default) for open access; `tools/server_deploy.py` takes the same key as `DEPLOY_API_KEY` for its health checks.

## Security Notes

- The dashboard (and the vLLM API it manages) is plain `http://` by default. With an API key set, the key travels **unencrypted** in the `X-API-Key` header and is stored in the browser's `localStorage` (readable by any script on the same origin or a malicious browser extension).
- On a trusted lab / data-center LAN this is acceptable. **If you expose the dashboard beyond that network, put a TLS-terminating reverse proxy (Nginx / Caddy) in front** — the app has no built-in TLS.
- Model launch configs (which may contain the vLLM API key) are stored in plaintext under `data/model_launch_configs/` (directory mode 700).

## Installation

One-line install (fetches the source, installs dependencies, and builds the frontend):

Linux / macOS:

```bash
curl -fsSL https://cdn.jsdelivr.net/gh/DNM6613/vLLM-Dashboard@master/install.sh | bash
```

Windows:

```powershell
curl -fsSL https://cdn.jsdelivr.net/gh/DNM6613/vLLM-Dashboard@master/install.ps1 -o $env:TEMP\vd-install.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\vd-install.ps1
```

On machines without GitHub access (e.g. intranet hosts), point `VLLM_DASHBOARD_REPO` at a reachable source mirror, then fetch the install script from that mirror.

Docker remote deploy: `tools/server_deploy.py` packages the app locally, uploads it, builds the Docker image on the target, and swaps the container. Set the three required variables, then run:

```bash
export DEPLOY_TARGET=<server-IP>
export DEPLOY_USER=<ssh-user>
export SERVER_PASS=<ssh-password>
python tools/server_deploy.py deploy
```

The target server needs Docker and SSH; the local machine needs Python 3.10+ with `paramiko`. `verify` re-checks a running deployment and `probe` shows its current state.

With an existing repository clone, install manually: run `bash native_install.sh` on Linux / macOS, or `powershell -ExecutionPolicy Bypass -File native_install.ps1` on Windows (a *local native* uvicorn install only — the production docker deploy is `tools/server_deploy.py`). After installation, edit `.env` (created from `.env.example` on first run) to configure your settings.

## Quick Start

```bash
bash start.sh   # Linux / macOS
```

```powershell
.\start.ps1     # Windows
```

Open http://localhost:5174 in your browser.
