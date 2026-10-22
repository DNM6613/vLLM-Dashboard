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
from .service.request_log import BODY_PREVIEW_CAP, request_log
from .ws import ticket as ws_ticket

load_dotenv()

logger = logging.getLogger(__name__)

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "backend_request_id", default="-")

class _RequestIdLogFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True

_API_KEY_QUERY_RE = re.compile(r"(api_key=|ticket=)[^&\s\"']+")

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
    # Audit 2026-10-20 P2-12: the root handler previously only carried the
    # request-id filter, so every backend.* logger logged to it UNREDACTED.
    _handler.addFilter(_RedactApiKeyFilter())

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
    # Unmatched /api/ or /assets/ path (404/405): the raw path (or its first
    # segments) would become an unbounded metric label — one request per
    # unique value creates a new series that prometheus_client never
    # deletes, so a curl loop can OOM the dashboard (audit 2026-10-20
    # P2-2). Bucket by the top-level segment instead.
    if path.startswith("/api/") or path.startswith("/assets/"):
        return "/" + path.split("/", 2)[1] + "/..."
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

# --- Access log (接入情况面板) -------------------------------------------

# The panel's own plumbing must not flood the log it serves.
_MONITOR_PATH_PREFIX = "/api/v1/monitor/"
_BODY_METHODS = ("POST", "PUT", "PATCH")
# /api/v1 endpoints that receive plaintext credentials in one body
# (api_key / ssh_password / bmc_password / hf_token — see the
# server_config payload): their bodies must NOT enter the request log,
# which GET /api/v1/monitor/requests returns verbatim to the connections
# panel (audit 2026-10-20 P2-1). The entry is still recorded, with
# body=None.
# Audit 2026-10-22 SEC-2: the launch-config body is credential-shaped too —
# start_command may carry --api-key and env_vars may carry VLLM_API_KEY,
# which would otherwise leak verbatim into the access log.
_CREDENTIAL_BODY_PATHS = ("/api/v1/config/server", "/api/v1/models/launch-config")
# Audit 2026-10-22 SEC-1: the access log is an in-memory + on-disk trail
# served verbatim to the connections panel, so an unbounded request body
# must not be read into memory (or into the log) just to preview it.
# Bodies at/above this cap are recorded WITHOUT a preview; the entry
# itself is still written.
_BODY_PREVIEW_MAX = 256 * 1024


def _sanitize_body(raw: bytes) -> str | None:
    if not raw:
        return None
    text = raw[:BODY_PREVIEW_CAP].decode("utf-8", "replace")
    return "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")


async def request_log_middleware(request: Request, call_next):
    path = request.url.path
    if (request.method == "OPTIONS"
            or not path.startswith("/api/v1")
            or path.startswith(_MONITOR_PATH_PREFIX)):
        return await call_next(request)
    body_preview = None
    if request.method in _BODY_METHODS and path not in _CREDENTIAL_BODY_PATHS:
        # Audit 2026-10-22 SEC-1: gate on Content-Length — a body at/above
        # the cap (or with no parsable length, e.g. chunked transfer) is
        # NOT read; the entry is still recorded, without a preview.
        try:
            content_length = int(request.headers.get("content-length") or 0)
        except ValueError:
            content_length = 0
        if 0 < content_length < _BODY_PREVIEW_MAX:
            try:
                body_preview = _sanitize_body(await request.body())
            except Exception:
                body_preview = None
    response = await call_next(request)
    ip = request.client.host if request.client else "?"
    request_log.record(ip, request.method, path, response.status_code, body_preview)
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
    """WS auth is a one-time ticket (B2): the API key no longer travels in
    the WS URL, so it can no longer leak via access logs / proxy logs."""
    if not _auth_enabled():
        return True
    provided = websocket.query_params.get("ticket", "")
    return bool(provided) and ws_ticket.ticket_store.consume(provided)

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
