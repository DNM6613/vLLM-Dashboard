from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from ..service.connection_notes import NOTE_MAX_LEN, connection_notes
from ..service.vllm_access_log import vllm_access_log

router = APIRouter(prefix="/api/v1/monitor", tags=["monitor"])


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=NOTE_MAX_LEN)


@router.get("/connections")
async def get_connections():
    """Per-IP vLLM client aggregates (with the persisted note), most recent first."""
    notes = connection_notes.all()
    rows = vllm_access_log.connections()
    for row in rows:
        row["note"] = notes.get(row["ip"], "")
    return {"connections": rows}

@router.get("/requests")
async def get_requests(
    ip: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
):
    """Recent vLLM request entries for one client IP."""
    return {"requests": vllm_access_log.requests_for(ip, limit)}

@router.put("/notes/{ip}")
async def set_connection_note(body: NoteIn, ip: str = Path(..., min_length=1, max_length=64)):
    """Set (or clear, when the note is blank) the per-IP access-panel note."""
    connection_notes.set(ip, body.note)
    return {"ok": True, "ip": ip, "note": body.note.strip()}
