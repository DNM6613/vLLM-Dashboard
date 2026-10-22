import contextlib
import copy
import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from uvicorn.config import LOGGING_CONFIG as _UVICORN_LOGGING_CONFIG

from . import __version__
from .api import (
    deployment_router,
    hardware_router,
    model_status_router,
    models_router,
    monitor_router,
    server_config_router,
    ws_auth_router,
)
from .config.remote_client import config_manager
from .config.settings import settings
from .console.session_manager import console_session_manager, websocket_console
from .middleware import (
    _error_response,
    _RedactApiKeyFilter,
    _RequestIdLogFilter,
    auth_middleware,
    http_exception_handler,
    request_id_metrics_middleware,
    request_log_middleware,
    unhandled_exception_handler,
    validation_exception_handler,
)
from .service.deployment_service import DATA_DIR
from .service.driver_reconcile_timer import (
    start_driver_reconcile_timer,
    stop_driver_reconcile_timer,
)
from .service.model_watchdog import start_watchdog, stop_watchdog
from .ws.hardware_broadcaster import hardware_broadcaster, websocket_hardware

load_dotenv()

logger = logging.getLogger(__name__)

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    await start_watchdog()
    await start_driver_reconcile_timer()
    try:
        yield
    finally:
        await stop_watchdog()
        await stop_driver_reconcile_timer()
        # Audit 2026-10-22 BCK-13: cancel pending start-verification probes
        # (plain 10 s delayed asyncio tasks) so they stop poking the remote
        # after the app is shutting down. Local import matches the
        # close_http_client pattern below (avoids an import at module load).
        from .service.model_service import stop_start_probes
        stop_start_probes()
        await hardware_broadcaster.shutdown()
        from .config.remote_client import close_http_client
        await close_http_client()

# Audit 2026-10-22 SEC-3 (user ruling: close all three): the interactive
# docs / OpenAPI schema endpoints serve the full route listing and are an
# information-leak surface on a LAN dashboard — the deploy verify now probes
# a concrete API route instead of /openapi.json (tools/server_deploy.py).
app = FastAPI(title="vLLM-Dashboard", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

app.middleware("http")(auth_middleware)
app.middleware("http")(request_id_metrics_middleware)
# Outermost: records every /api/v1 request (incl. 401s rejected by auth).
app.middleware("http")(request_log_middleware)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# Production is the single 5174 container (statics served by the backend;
# there is no vite dev server in the container). The 5173 dev origins were
# leftover dev-proxy entries (audit 2026-10-20 P3-13); dev machines running
# the vite proxy flow talk same-origin and need no CORS entry — set
# VLLM_DASHBOARD_CORS_ALL if a custom dev origin ever becomes necessary.
_cors_origins = [
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]
if os.environ.get("VLLM_DASHBOARD_CORS_ALL"):
    _cors_origins = ["*"]
    logger.warning(
        "VLLM_DASHBOARD_CORS_ALL is set: CORS now allows ALL origins "
        "(allow_credentials disabled). Only appropriate for a fully "
        "trusted network.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins[0] != "*",
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(model_status_router)
app.include_router(models_router)
app.include_router(hardware_router)
app.include_router(monitor_router)
app.include_router(server_config_router)
app.include_router(deployment_router)
app.include_router(ws_auth_router)

app.add_api_websocket_route("/ws/hardware", websocket_hardware)
app.add_api_websocket_route("/ws/console", websocket_console)

@app.get("/health")
def health_check():
    # Audit 2026-10-22 INF-3: readiness probe — the deploy pipeline's health
    # gate (tools/server_deploy.py) treats any non-"healthy" body as a
    # failure and rolls back to the previous image (INF-2), so a container
    # that is up but broken (frontend bundle missing, data dir not
    # writable) must not pass the gate and only 404 for users.
    problems: list[str] = []
    # Static-file serving is only declared via the STATIC_DIR env var (the
    # deploy script always sets it in the container); local dev serves the
    # frontend via vite and must not be judged degraded.
    if os.environ.get("STATIC_DIR") and not os.path.exists(
            os.path.join(_STATIC_DIR, "index.html")):
        problems.append("static index.html missing")
    if not (os.path.isdir(DATA_DIR) and os.access(DATA_DIR, os.W_OK)):
        problems.append("data dir missing or not writable")
    if problems:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "version": __version__, "problems": problems},
        )
    config = config_manager.get_config()
    return {
        "status": "healthy",
        "version": __version__,
        "mode": "remote" if config.is_remote() else "local",
        "console_sessions": console_session_manager.active_session_count(),
    }

@app.get("/metrics")
def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

_STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.getcwd(), "static"))

_assets_dir = os.path.join(_STATIC_DIR, "assets")
if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str, request: Request):
    if full_path in ("api", "ws", "health") or full_path.startswith(("api/", "ws/")):
        return _error_response(404, "not_found", "Not Found", request)
    index_path = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return _error_response(404, "not_found", "Frontend not built", request)

def _build_log_config() -> dict:
    cfg = copy.deepcopy(_UVICORN_LOGGING_CONFIG)
    cfg["filters"] = {"redact_api_key": {"()": "backend.middleware._RedactApiKeyFilter"}}
    for _handler in cfg["handlers"].values():
        _handler["filters"] = ["redact_api_key"]
    return cfg

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s")
    for _handler in logging.getLogger().handlers:
        _handler.addFilter(_RequestIdLogFilter())
        # Audit 2026-10-20 P2-12: uvicorn's log_config reconfigures only the
        # uvicorn.* loggers — backend.* records still flow through this root
        # handler, so it must redact api_key=/ticket= like the uvicorn side.
        _handler.addFilter(_RedactApiKeyFilter())
    logger.info("Starting vLLM-Dashboard API Server")

    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=False,
        log_config=_build_log_config()
    )

if __name__ == "__main__":
    main()
