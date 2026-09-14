# vLLM-Dashboard 一行安装器（Windows）
#
#   curl -fsSLk https://10.131.1.14/uforce/vLLM-Dashboard/raw/master/install.ps1 -o $env:TEMP\vd-install.ps1
#   powershell -ExecutionPolicy Bypass -File $env:TEMP\vd-install.ps1
#
# 可选参数: 安装目录（默认 %USERPROFILE%\vLLM-Dashboard）
# 可选环境变量: VLLM_DASHBOARD_GITEA（默认 https://10.131.1.14）、VLLM_DASHBOARD_BRANCH（默认 master）
param([string]$InstallDir = "")

$ErrorActionPreference = "Stop"

$Gitea = if ($env:VLLM_DASHBOARD_GITEA) { $env:VLLM_DASHBOARD_GITEA } else { "https://10.131.1.14" }
$RepoPath = "uforce/vLLM-Dashboard"
$Branch = if ($env:VLLM_DASHBOARD_BRANCH) { $env:VLLM_DASHBOARD_BRANCH } else { "master" }
if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE "vLLM-Dashboard" }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  vLLM-Dashboard 安装脚本 (Windows)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Write-Host "`n[1/5] 获取源码..." -ForegroundColor Yellow
if (Test-Path (Join-Path $InstallDir "deploy.sh")) {
    Write-Host "  ✓ 发现已有安装: $InstallDir（跳过下载）" -ForegroundColor Green
} else {
    $tmpTar = Join-Path $env:TEMP "vllm-dashboard-install.tar.gz"
    Write-Host "  下载 $Gitea/$RepoPath (branch: $Branch) ..."
    curl.exe -fsSLk "$Gitea/$RepoPath/archive/$Branch.tar.gz" -o $tmpTar
    if ($LASTEXITCODE -ne 0) { Write-Host "  ✗ 下载失败" -ForegroundColor Red; exit 1 }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    tar.exe -xzf $tmpTar -C $InstallDir --strip-components=1
    Remove-Item $tmpTar
    Write-Host "  ✓ 源码已解压到 $InstallDir" -ForegroundColor Green
}
Set-Location $InstallDir

Write-Host "`n[2/5] 检查 Python 安装..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  ✓ Python 已安装: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  ✗ Python 未安装或未添加到 PATH" -ForegroundColor Red
    Write-Host "  请先安装 Python 3.10+: https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

Write-Host "`n[3/5] 创建虚拟环境并安装依赖..." -ForegroundColor Yellow
python -m venv venv
& ".\venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip
pip install -r requirements.txt
if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✓ 依赖安装成功" -ForegroundColor Green
} else {
    Write-Host "  ✗ 依赖安装失败" -ForegroundColor Red
    exit 1
}

Write-Host "`n[4/5] 创建配置文件..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  ✓ 配置文件已创建 (.env)" -ForegroundColor Green
    Write-Host "  请编辑 .env 文件配置您的参数" -ForegroundColor Yellow
} else {
    Write-Host "  ✓ 配置文件已存在" -ForegroundColor Green
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  安装完成!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`n启动服务:" -ForegroundColor White
Write-Host "  cd $InstallDir" -ForegroundColor Gray
Write-Host "  .\start.ps1" -ForegroundColor Gray
Write-Host "`n或手动:" -ForegroundColor White
Write-Host "  .\venv\Scripts\activate" -ForegroundColor Gray
Write-Host "  uvicorn backend.main:app --host 0.0.0.0 --port 5174" -ForegroundColor Gray
Write-Host "`n访问地址:" -ForegroundColor White
Write-Host "  http://localhost:5174" -ForegroundColor Cyan
Write-Host "  http://localhost:5174/docs (Swagger API)" -ForegroundColor Cyan
