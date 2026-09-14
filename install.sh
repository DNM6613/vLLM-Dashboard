#!/bin/bash
# vLLM-Dashboard 一行安装器（Linux / macOS）
#
#   curl -fsSL https://cdn.jsdelivr.net/gh/DNM6613/vLLM-Dashboard@master/install.sh | bash
#
# 可选参数: 安装目录（默认 ~/vLLM-Dashboard）
# 可选环境变量: VLLM_DASHBOARD_REPO（默认 https://github.com/DNM6613/vLLM-Dashboard，可指向内网镜像）、
#               VLLM_DASHBOARD_BRANCH（默认 master）
set -euo pipefail

REPO="${VLLM_DASHBOARD_REPO:-https://github.com/DNM6613/vLLM-Dashboard}"
BRANCH="${VLLM_DASHBOARD_BRANCH:-master}"
INSTALL_DIR="${1:-$HOME/vLLM-Dashboard}"

echo "========================================"
echo "  vLLM-Dashboard 安装脚本 (Linux / macOS)"
echo "========================================"

echo -e "\n[1/5] 获取源码..."
if [ -f "$INSTALL_DIR/deploy.sh" ]; then
    echo "  ✓ 发现已有安装: $INSTALL_DIR（跳过下载）"
else
    tmpTar=$(mktemp)
    trap 'rm -f "$tmpTar"' EXIT
    echo "  下载 $REPO (branch: $BRANCH) ..."
    CURL_ARGS=(-fsSL)
    repoHost="${REPO#*://}"; repoHost="${repoHost%%/*}"
    [[ "$repoHost" =~ ^[0-9.]+$ ]] && CURL_ARGS+=(-k)
    curl "${CURL_ARGS[@]}" "$REPO/archive/$BRANCH.tar.gz" -o "$tmpTar"
    mkdir -p "$INSTALL_DIR"
    tar xzf "$tmpTar" -C "$INSTALL_DIR" --strip-components=1
    echo "  ✓ 源码已解压到 $INSTALL_DIR"
fi
cd "$INSTALL_DIR"

echo -e "\n[2/5] 检查 Python 安装..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo "  ✓ Python 已安装: $PYTHON_VERSION"
else
    echo "  ✗ Python 未安装"
    echo "  请先安装 Python 3.10+: https://www.python.org/downloads/"
    exit 1
fi

echo -e "\n[3/5] 创建虚拟环境并安装依赖..."
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo "  ✓ 依赖安装成功"

echo -e "\n[4/5] 创建配置文件..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "  ✓ 配置文件已创建 (.env)"
    echo "  请编辑 .env 文件配置您的参数"
else
    echo "  ✓ 配置文件已存在"
fi

echo -e "\n[5/5] 构建前端（原生部署需要前端产物，否则 5174 仅 API + /docs）..."
if [ -d "frontend" ] && command -v node &> /dev/null && command -v npm &> /dev/null; then
    (cd frontend && npm install && npm run build)
    rm -rf static
    cp -r frontend/dist static
    echo "  ✓ 前端构建完成（frontend/dist -> static/，STATIC_DIR 默认指向）"
else
    echo "  ⚠ node/npm 不可用或 frontend 目录缺失，已跳过前端构建"
    echo "  原生部署需先构建前端，否则 http://localhost:5174 仅提供 API 与 /docs"
    echo "  安装 Node.js 18+ 后重跑本脚本，或手动执行: cd frontend && npm install && npm run build && cp -r dist ../static"
fi

echo -e "\n========================================"
echo "  安装完成!"
echo "========================================"
echo -e "\n启动服务:"
echo "  cd $INSTALL_DIR"
echo "  bash start.sh"
echo -e "\n或手动:"
echo "  source venv/bin/activate"
echo "  uvicorn backend.main:app --host 0.0.0.0 --port 5174"
echo -e "\n访问地址:"
echo -e "  http://localhost:5174"
echo -e "  http://localhost:5174/docs (Swagger API)"
