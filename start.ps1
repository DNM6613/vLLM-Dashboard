[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 > $null

Write-Host "========================================"
Write-Host "  vLLM-Dashboard"
Write-Host "========================================"

$envFile = Join-Path $PSScriptRoot ".env"
$apiHost = "0.0.0.0"
$apiPort = 5174
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*API_HOST\s*=\s*(.+)\s*$') { $apiHost = $matches[1].Trim() }
        elseif ($_ -match '^\s*API_PORT\s*=\s*(\d+)\s*$') { $apiPort = [int]$matches[1] }
    }
}

$pythonExe = "python"
$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (Test-Path $venvPython) { $pythonExe = $venvPython }

Write-Host ""
Write-Host "Cleaning up old processes..."
Write-Host "  WARNING: about to terminate processes listening on port 5173 and port $apiPort (they may be other dev services)..."
foreach ($port in @($apiPort, 5173)) {
    $oldProcs = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($oldPid in $oldProcs) {
                Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
                Write-Host "  Killed PID $oldPid (port $port)"
    }
}
Start-Sleep -Milliseconds 500

Write-Host ""
Write-Host "[1/2] Backend (port $apiPort)..."
$uvicornArgs = @("-m", "uvicorn", "backend.main:app", "--host", $apiHost, "--port", $apiPort)
if ($env:VLLM_DASHBOARD_DEV -eq "1") {
    $uvicornArgs += "--reload"
    Write-Host "  (dev mode: --reload enabled)"
}
$backend = Start-Process -FilePath $pythonExe -ArgumentList $uvicornArgs -PassThru -WindowStyle Normal
Write-Host "  PID: $($backend.Id)"

Write-Host "  Waiting..." -NoNewline
$ready = $false
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    if ($backend.HasExited) {
        Write-Host ""
        Write-Host "  Backend process exited unexpectedly (exit code: $($backend.ExitCode))" -ForegroundColor Red
        break
    }
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", $apiPort)
        if ($tcp.Connected) { $ready = $true; $tcp.Close(); break }
        $tcp.Close()
    } catch {}
    Start-Sleep -Milliseconds 500
    Write-Host "." -NoNewline
}
Write-Host ""
if ($ready) { Write-Host "  OK" } else { Write-Host "  Timeout, continue anyway" }

Write-Host ""
Write-Host "[2/2] Frontend (port 5173)..."
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    Write-Host "  ERROR: npm not found on PATH. Install Node.js 18+ first: https://nodejs.org/" -ForegroundColor Red
    Write-Host "  Frontend will not start (backend on port $apiPort keeps running). Re-run this script after installing Node.js" -ForegroundColor Red
    exit 1
}
$nodeModulesDir = Join-Path $PSScriptRoot "frontend\node_modules"
if (-not (Test-Path $nodeModulesDir)) {
    Write-Host "  frontend\node_modules not found, running npm install first..." -ForegroundColor Yellow
    Push-Location (Join-Path $PSScriptRoot "frontend")
    npm install
    Pop-Location
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: npm install failed, frontend cannot start" -ForegroundColor Red
        exit 1
    }
    Write-Host "  npm install done"
}
Push-Location (Join-Path $PSScriptRoot "frontend")
$frontend = Start-Process -FilePath "cmd" -ArgumentList "/c", "npm run dev" -PassThru -WindowStyle Normal
Pop-Location
Write-Host "  PID: $($frontend.Id)"

Write-Host ""
Write-Host "========================================"
Write-Host "  Started"
Write-Host "========================================"
Write-Host ""
Write-Host "  Backend:  http://localhost:$apiPort"
Write-Host "  Frontend: http://localhost:5173"
Write-Host "  API Docs: http://localhost:$apiPort/docs"
Write-Host ""
Write-Host "  Press any key to stop all services..."

$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

Write-Host ""
Write-Host "Stopping..."

function Stop-ProcessTree($processId) {
    if (-not $processId) { return }
    try {
        $proc = Get-Process -Id $processId -ErrorAction Stop
        $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $processId }
        foreach ($child in $children) {
            Stop-ProcessTree $child.ProcessId
        }
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Write-Host "  Stopped PID $processId"
    } catch {
    }
}

Stop-ProcessTree $backend.Id

if ($frontend.Id) {
    try {
        $cmdProc = Get-Process -Id $frontend.Id -ErrorAction Stop
        $npmChildren = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $frontend.Id }
        foreach ($npmChild in $npmChildren) {
            Stop-ProcessTree $npmChild.ProcessId
        }
        Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  Stopped frontend PID $($frontend.Id)"
    } catch {}
    try {
        $portProcs = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $portProcs) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host "  Stopped node PID $procId (port 5173)"
        }
    } catch {}
}

Write-Host "Stopped"
