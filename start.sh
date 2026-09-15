#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

echo "========================================"
echo "  vLLM-Dashboard"
echo "========================================"

envFile=".env"
apiHost="0.0.0.0"
apiPort=5174
if [ -f "$envFile" ]; then
    apiHost=$(grep -E '^[[:space:]]*API_HOST[[:space:]]*=' "$envFile" | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)
    apiPort=$(grep -E '^[[:space:]]*API_PORT[[:space:]]*=' "$envFile" | head -n1 | cut -d= -f2- | tr -d '[:space:]' || true)
    [ -n "$apiHost" ] || apiHost="0.0.0.0"
    case "$apiPort" in (*[!0-9]*|"") apiPort=5174;; esac
fi

pythonExe="python3"
venvPython="venv/bin/python"
if [ -x "$venvPython" ]; then pythonExe="$venvPython"; fi

# A process on our ports is only ours if its command line matches the
# dashboard backend (uvicorn backend.main:app) or a vite dev server under
# this project directory. Anything else is left untouched.
is_ours() {
    local args
    args=$(ps -p "$1" -o args= 2>/dev/null) || return 1
    case "$args" in
        *backend.main:app*) return 0 ;;
        *"$PWD"*vite*) return 0 ;;
        *vLLM-Dashboard*vite*) return 0 ;;
        *) return 1 ;;
    esac
}

echo ""
echo "Cleaning up old vLLM-Dashboard processes..."
echo "  (only processes whose command line matches this project are killed; other services on these ports are left running)"
if command -v lsof &> /dev/null; then
    for port in 5173 "$apiPort"; do
        pids=$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null | sort -u || true)
        for pid in $pids; do
            if is_ours "$pid"; then
                kill -9 "$pid" 2>/dev/null || true
                echo "  Killed PID $pid (port $port)"
            else
                echo "  Skipping PID $pid (port $port): not a vLLM-Dashboard process"
            fi
        done
    done
else
    echo "  lsof not found, skipping port cleanup"
fi
sleep 1

echo ""
echo "[1/2] Backend (port $apiPort)..."
uvicornArgs=(-m uvicorn backend.main:app --host "$apiHost" --port "$apiPort")
if [ "${VLLM_DASHBOARD_DEV:-}" = "1" ]; then
    uvicornArgs+=(--reload)
    echo "  (dev mode: --reload enabled)"
fi
"$pythonExe" "${uvicornArgs[@]}" &
backendPid=$!
echo "  PID: $backendPid"

printf "  Waiting..."
ready=false
deadline=$((SECONDS + 20))
while [ $SECONDS -lt $deadline ]; do
    if kill -0 "$backendPid" 2>/dev/null; then
        if (exec 3<>"/dev/tcp/127.0.0.1/$apiPort") 2>/dev/null; then
            ready=true
            break
        fi
    else
        wait "$backendPid" || true
        echo ""
        echo "  Backend process exited unexpectedly"
        break
    fi
    printf "."
    sleep 1
done
echo ""
if [ "$ready" = true ]; then echo "  OK"; else echo "  Timeout, continue anyway"; fi

echo ""
echo "[2/2] Frontend (port 5173)..."
if ! command -v npm &> /dev/null; then
    echo "  ERROR: npm not found on PATH. Install Node.js 18+ first: https://nodejs.org/"
    echo "  Frontend will not start (backend on port $apiPort keeps running). Re-run this script after installing Node.js"
    exit 1
fi
if [ ! -d "frontend/node_modules" ]; then
    echo "  frontend/node_modules not found, running npm install first..."
    (cd frontend && npm install)
    echo "  npm install done"
fi
(cd frontend && npm run dev) &
frontendPid=$!
echo "  PID: $frontendPid"

echo ""
echo "========================================"
echo "  Started"
echo "========================================"
echo ""
echo "  Backend:  http://localhost:$apiPort"
echo "  Frontend: http://localhost:5173"
echo "  API Docs: http://localhost:$apiPort/docs"
echo ""
echo "  Press Enter to stop all services..."

read -r || true

echo ""
echo "Stopping..."

kill_tree() {
    local pid=$1
    local children
    children=$(pgrep -P "$pid" 2>/dev/null || true)
    for child in $children; do
        kill_tree "$child"
    done
    if kill "$pid" 2>/dev/null; then
        echo "  Stopped PID $pid"
    fi
}

kill_tree "$backendPid"

kill_tree "$frontendPid"

if command -v lsof &> /dev/null; then
    for procId in $(lsof -ti tcp:5173 -sTCP:LISTEN 2>/dev/null | sort -u); do
        if is_ours "$procId" && kill -9 "$procId" 2>/dev/null; then
            echo "  Stopped node PID $procId (port 5173)"
        fi
    done
fi

echo "Stopped"
