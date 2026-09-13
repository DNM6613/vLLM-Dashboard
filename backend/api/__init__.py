from .hardware import router as hardware_router
from .model_status import router as model_status_router
from .models import router as models_router
from .server_config import router as server_config_router

__all__ = [
    "hardware_router",
    "model_status_router",
    "models_router",
    "server_config_router",
]
