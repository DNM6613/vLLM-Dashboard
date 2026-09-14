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

With an existing repository clone, install manually: run `bash deploy.sh` on Linux / macOS, or `powershell -ExecutionPolicy Bypass -File deploy.ps1` on Windows. After installation, edit `.env` (created from `.env.example` on first run) to configure your settings.

## Quick Start

```bash
bash start.sh   # Linux / macOS
```

```powershell
.\start.ps1     # Windows
```

Open http://localhost:5174 in your browser.
