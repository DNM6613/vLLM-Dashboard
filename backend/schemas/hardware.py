from pydantic import BaseModel, Field


class GPUInfo(BaseModel):
    index: int = Field(ge=0)
    name: str
    memory_used_mb: int = Field(ge=0)
    memory_total_mb: int = Field(ge=0)
    temperature_c: int = Field(ge=0, le=200)
    power_draw_w: int = Field(ge=0)
    power_limit_w: int = Field(ge=0)
    utilization_gpu: int = Field(default=0, ge=0, le=100)
    utilization_memory: int = Field(default=0, ge=0, le=100)
