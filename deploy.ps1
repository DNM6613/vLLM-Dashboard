
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  vLLM-Dashboard 部署脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Write-Host "`n[1/4] 检查 Python 安装..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  ✓ Python 已安装: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  ✗ Python 未安装或未添加到 PATH" -ForegroundColor Red
    Write-Host "  请先安装 Python 3.10+: https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

Write-Host "`n[2/4] 创建虚拟环境..." -ForegroundColor Yellow
if (Test-Path "venv") {
    Write-Host "  发现已有虚拟环境，是否删除重建? (y/n)" -ForegroundColor Yellow
    $choice = Read-Host
    if ($choice -eq "y") {
        Remove-Item -Recurse -Force "venv"
        python -m venv venv
        Write-Host "  ✓ 虚拟环境已重建" -ForegroundColor Green
    }
} else {
    python -m venv venv
    Write-Host "  ✓ 虚拟环境已创建" -ForegroundColor Green
}

Write-Host "`n[3/4] 安装依赖..." -ForegroundColor Yellow
& ".\venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip
pip install -r requirements.txt

if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✓ 依赖安装成功" -ForegroundColor Green
} else {
    Write-Host "  ✗ 依赖安装失败" -ForegroundColor Red
    exit 1
}

Write-Host "`n[4/4] 创建配置文件..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  ✓ 配置文件已创建 (.env)" -ForegroundColor Green
    Write-Host "  请编辑 .env 文件配置您的参数" -ForegroundColor Yellow
} else {
    Write-Host "  ✓ 配置文件已存在" -ForegroundColor Green
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  部署完成!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`n启动服务:" -ForegroundColor White
Write-Host "  .\venv\Scripts\activate" -ForegroundColor Gray
Write-Host "  python -m backend.main" -ForegroundColor Gray
Write-Host "`n或使用 uvicorn:" -ForegroundColor White
Write-Host "  uvicorn backend.main:app --host 0.0.0.0 --port 5174 --reload" -ForegroundColor Gray
Write-Host "`n访问地址:" -ForegroundColor White
Write-Host "  http://localhost:5174" -ForegroundColor Cyan
Write-Host "  http://localhost:5174/docs (Swagger API)" -ForegroundColor Cyan
