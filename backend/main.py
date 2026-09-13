import contextlib
import copy
import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from uvicorn.config import LOGGING_CONFIG as _UVICORN_LOGGING_CONFIG

from . import __version__
from .api import hardware_router, model_status_router, models_router, server_config_router
from .config.remote_client import config_manager
from .config.settings import settings
from .console.session_manager import console_session_manager, websocket_console
from .middleware import (
    _error_response,
    _RequestIdLogFilter,
    auth_middleware,
    http_exception_handler,
    request_id_metrics_middleware,
    unhandled_exception_handler,
    validation_exception_handler,
)
from .ws.hardware_broadcaster import hardware_broadcaster, websocket_hardware

load_dotenv()

logger = logging.getLogger(__name__)

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await hardware_broadcaster.shutdown()
    from .config.remote_client import close_http_client
    await close_http_client()

app = FastAPI(title="vLLM-Dashboard", version=__version__, lifespan=lifespan)

app.middleware("http")(auth_middleware)
app.middleware("http")(request_id_metrics_middleware)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

_cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
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
app.include_router(server_config_router)

app.add_api_websocket_route("/ws/hardware", websocket_hardware)
app.add_api_websocket_route("/ws/console", websocket_console)

@app.get("/health")
def health_check():
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
