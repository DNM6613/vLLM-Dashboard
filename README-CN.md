# vLLM-Dashboard

[English](README.md)

vLLM 推理服务器管理面板：通过浏览器远程实时掌握模型与服务器硬件状态。可便捷配置环境变量和模型启动参数，整合基于SSH的控制台便于服务器配置管理。

## 功能

- **模型管理**：查看已加载模型，一键启动 / 停止推理服务，配置启动参数
- **实时监控**：模型运行中 / 排队请求数，GPU、CPU、内存等硬件指标实时刷新
- **模型下载**：从 Hugging Face 拉取模型，进度可视
- **性能测试**：内置基准测试入口
- **Web 终端**：内置 SSH 控制台，无需额外客户端
- **电源管理**：通过 BMC / IPMI 远程开关机与查看电源状态；无 BMC / IPMI 时仅支持经 SSH 远程关机
- **服务器配置**：统一管理目标服务器的 SSH、推理服务 API、BMC 凭据
- **访问保护**：API Key 门禁；界面支持中英文与明暗主题

## 适用范围

- 自托管的单台 LLM 推理服务器，如实验室 / 机房 AI 服务器的调试与运维

## 适配环境

- **部署环境**：Linux / Windows 系统，Docker 或 Python 3.10+ 环境
- **管理目标**：Linux 服务器，启用 SSH 服务，运行 vLLM 推理引擎，NVIDIA GPU + CUDA 环境（因没有测试开发平台，暂不支持其他品牌）
- **浏览器**：Chrome / Edge / Firefox 等现代浏览器

## 安装

一键安装（自动拉取源码、安装依赖并构建前端）：

Linux / macOS:

```bash
curl -fsSL https://cdn.jsdelivr.net/gh/DNM6613/vLLM-Dashboard@master/install.sh | bash
```

Windows:

```powershell
curl -fsSL https://cdn.jsdelivr.net/gh/DNM6613/vLLM-Dashboard@master/install.ps1 -o $env:TEMP\vd-install.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\vd-install.ps1
```

无法访问 GitHub 的机器（如内网），先设置 `VLLM_DASHBOARD_REPO` 指向可访问的源码镜像，再从镜像获取安装脚本运行。

Docker 远程部署：`tools/server_deploy.py` 在本地打包、上传，在目标机构建 Docker 镜像并替换容器。设置三个必填变量后运行：

```bash
export DEPLOY_TARGET=<服务器IP>
export DEPLOY_USER=<SSH用户名>
export SERVER_PASS=<SSH密码>
python tools/server_deploy.py deploy
```

目标服务器需 Docker 与 SSH；本机需 Python 3.10+ 及 `paramiko`。`verify` 复检已运行的部署，`probe` 查看当前状态。

已有仓库副本时可手动安装：Linux / macOS 运行 `bash deploy.sh`，Windows 运行 `powershell -ExecutionPolicy Bypass -File deploy.ps1`。安装后编辑 `.env`（首次自动从 `.env.example` 复制）配置参数。

## 快速开始

```bash
bash start.sh   # Linux / macOS
```

```powershell
.\start.ps1     # Windows
```

浏览器打开 http://localhost:5174。
