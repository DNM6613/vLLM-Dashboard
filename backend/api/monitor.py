from fastapi import APIRouter, Query

from ..service.request_log import request_log

router = APIRouter(prefix="/api/v1/monitor", tags=["monitor"])

@router.get("/connections")
async def get_connections():
    """Per-IP access aggregates: who is calling, how often, most recent."""
    return {"connections": request_log.connections()}

@router.get("/requests")
async def get_requests(
    ip: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
):
    """Recent request entries (with body previews) for one client IP."""
    return {"requests": request_log.requests_for(ip, limit)}
