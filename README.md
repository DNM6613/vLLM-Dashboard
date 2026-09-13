# vLLM-Dashboard

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

- **Deployment**: Linux / Windows, Docker or Python 3.10+
- **Managed target**: Linux server with SSH enabled, running the vLLM engine on NVIDIA GPU + CUDA (other GPU brands not supported yet — no test platform available)
- **Browser**: Chrome / Edge / Firefox and other modern browsers
