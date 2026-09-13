from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ModelStatus(StrEnum):
    DOWNLOADED = "downloaded"
    LOADING = "loading"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"

class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    path: str

class ModelInfo(BaseModel):
    id: str
    name: str
    path: str
    status: ModelStatus
    config: ModelConfig
    created_at: datetime
    updated_at: datetime
    download_progress: float = 0.0
    error_message: str = ""
    size_bytes: int = 0
    loading_since: datetime | None = None
