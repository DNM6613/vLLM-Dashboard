#!/bin/bash
set -euo pipefail

echo "========================================"
echo "  vLLM-Dashboard 部署脚本"
echo "========================================"

echo -e "\n[1/5] 检查 Python 安装..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo "  ✓ Python 已安装: $PYTHON_VERSION"
else
    echo "  ✗ Python 未安装"
    echo "  请先安装 Python 3.10+: https://www.python.org/downloads/"
    exit 1
fi

echo -e "\n[2/5] 创建虚拟环境..."
if [ -d "venv" ]; then
    read -p "发现已有虚拟环境，是否删除重建? (y/n): " choice
    if [ "$choice" = "y" ]; then
        rm -rf venv
        python3 -m venv venv
        echo "  ✓ 虚拟环境已重建"
    fi
else
    python3 -m venv venv
    echo "  ✓ 虚拟环境已创建"
fi

echo -e "\n[3/5] 安装依赖..."
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
echo "  部署完成!"
echo "========================================"
echo -e "\n启动服务:"
echo "  source venv/bin/activate"
echo "  python -m backend.main"
echo -e "\n或使用 uvicorn:"
echo "  uvicorn backend.main:app --host 0.0.0.0 --port 5174 --reload"
echo -e "\n访问地址:"
echo -e "  http://localhost:5174"
echo -e "  http://localhost:5174/docs (Swagger API)"
