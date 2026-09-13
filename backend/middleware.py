import contextvars
import hmac
import logging
import os
import re
import time
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram

from .config.settings import settings

load_dotenv()

logger = logging.getLogger(__name__)

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "backend_request_id", default="-")

class _RequestIdLogFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True

_API_KEY_QUERY_RE = re.compile(r"(api_key=)[^&\s\"']+")

class _RedactApiKeyFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if args and isinstance(args, tuple):
            record.args = tuple(
                _API_KEY_QUERY_RE.sub(r"\1***", a) if isinstance(a, str) else a
                for a in args
            )
        return True

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s"
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_RequestIdLogFilter())

logging.getLogger("paramiko").setLevel(logging.CRITICAL)

http_requests_total = Counter("http_requests_total", "Total HTTP requests", ["method", "path", "status"])
http_latency_seconds = Histogram("http_latency_seconds", "HTTP request latency in seconds", ["method", "path"])
ws_connections_total = Counter("ws_connections_total", "Total WebSocket connections", ["ws"])

_HTTP_STATUS_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 409: "conflict", 422: "validation_error",
    500: "internal_error", 502: "bad_gateway", 503: "service_unavailable",
}

def _error_response(status_code: int, code: str, message: str, request: Request, details=None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id} if request_id else {},
    )

def _normalize_metric_path(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path:
        return route_path
    path = request.url.path
    if path.startswith("/api/"):
        parts = path.split("/")
        if len(parts) > 4:
            return "/".join(parts[:4]) + "/..."
    return path

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

def _resolve_request_id(request: Request) -> str:
    client_id = request.headers.get("X-Request-ID")
    if client_id and _REQUEST_ID_PATTERN.match(client_id):
        return client_id
    return uuid4().hex[:12]

async def request_id_metrics_middleware(request: Request, call_next):
    request_id = _resolve_request_id(request)
    request.state.request_id = request_id
    request_id_var.set(request_id)
    start = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - start
    path = _normalize_metric_path(request)
    http_requests_total.labels(method=request.method, path=path, status=response.status_code).inc()
    http_latency_seconds.labels(method=request.method, path=path).observe(elapsed)
    response.headers["X-Request-ID"] = request_id
    return response

def _auth_enabled() -> bool:
    return bool(settings.API_KEY)

def _requires_auth(path: str) -> bool:
    p = path.lstrip("/")
    if p in ("health", "metrics", "api", "ws"):
        return True
    return p.startswith(("api/", "ws/"))

async def auth_middleware(request: Request, call_next):
    if not _auth_enabled() or not _requires_auth(request.url.path):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    provided = request.headers.get("X-API-Key", "")
    if provided and hmac.compare_digest(provided, settings.API_KEY):
        return await call_next(request)
    return _error_response(401, "unauthorized", "Invalid or missing API key", request)

def ws_api_key_valid(websocket: WebSocket) -> bool:
    if not _auth_enabled():
        return True
    provided = websocket.query_params.get("api_key", "")
    return bool(provided) and hmac.compare_digest(provided, settings.API_KEY)

async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        message, details = exc.detail, None
    else:
        message, details = str(exc.detail), exc.detail
    code = _HTTP_STATUS_CODES.get(exc.status_code, f"http_{exc.status_code}")
    return _error_response(exc.status_code, code, message, request, details)

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error_response(422, "validation_error", "Request validation failed", request, {"errors": exc.errors()})

async def unhandled_exception_handler(request: Request, exc: Exception):
    method = getattr(request, "method", None)
    if method is None:
        method = "WS" if request.scope.get("type") == "websocket" else "?"
    logger.exception(f"Unhandled exception {method} {request.url.path}: {exc}")
    return _error_response(500, "internal_error", "Internal server error", request)
