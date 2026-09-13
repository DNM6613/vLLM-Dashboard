import atexit
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from ..schemas.model import ModelConfig, ModelInfo, ModelStatus

logger = logging.getLogger(__name__)

_TRANSIENT_STATES = {ModelStatus.RUNNING, ModelStatus.LOADING}

_DEBOUNCE_SECONDS = 1.0

_ALLOWED_TRANSITIONS: dict[ModelStatus, frozenset[ModelStatus]] = {
    ModelStatus.STOPPED: frozenset({
        ModelStatus.STOPPED, ModelStatus.LOADING, ModelStatus.RUNNING,
        ModelStatus.DOWNLOADED, ModelStatus.FAILED}),
    ModelStatus.LOADING: frozenset({
        ModelStatus.LOADING, ModelStatus.RUNNING, ModelStatus.STOPPED,
        ModelStatus.FAILED}),
    ModelStatus.RUNNING: frozenset({
        ModelStatus.RUNNING, ModelStatus.STOPPED, ModelStatus.FAILED}),
    ModelStatus.FAILED: frozenset({
        ModelStatus.FAILED, ModelStatus.LOADING, ModelStatus.RUNNING,
        ModelStatus.STOPPED, ModelStatus.DOWNLOADED}),
    ModelStatus.DOWNLOADED: frozenset({
        ModelStatus.DOWNLOADED, ModelStatus.LOADING, ModelStatus.RUNNING,
        ModelStatus.STOPPED, ModelStatus.FAILED}),
}

class StateMachine:

    def __init__(self, state_file: str | None = None):
        self.models: dict[str, ModelInfo] = {}
        self.current_model_id: str | None = None
        self._lock = threading.RLock()
        self._state_file = (
            Path(state_file) if state_file
            else Path(__file__).resolve().parent.parent.parent / "data" / "models_state.json"
        )
        self._debounce_timer: threading.Timer | None = None
        self._dirty = False
        self._load_state()
        atexit.register(self._flush_state)

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            with open(self._state_file, encoding="utf-8") as f:
                data = json.load(f)
            for model_id, model_data in data.get("models", {}).items():
                try:
                    if model_data.get("status") in _TRANSIENT_STATES:
                        model_data["status"] = ModelStatus.STOPPED
                        model_data["loading_since"] = None
                    model_data["download_progress"] = 0.0
                    model_data["error_message"] = ""
                    self.models[model_id] = ModelInfo(**model_data)
                except Exception as e:
                    logger.warning(f"模型 {model_id} 状态恢复失败，保留为 FAILED: {e}")
                    try:
                        self.models[model_id] = ModelInfo(
                            id=model_id,
                            name=model_data.get("name", model_id),
                            path=model_data.get("path", ""),
                            status=ModelStatus.FAILED,
                            config=ModelConfig(
                                name=model_data.get("name", model_id),
                                path=model_data.get("path", ""),
                            ),
                            created_at=datetime.now(),
                            updated_at=datetime.now(),
                            error_message=f"状态恢复失败: {e}",
                        )
                    except Exception:
                        logger.error(f"无法为模型 {model_id} 创建 FAILED 条目，跳过")
            self.current_model_id = data.get("current_model_id")
            if self.current_model_id and self.models.get(self.current_model_id):
                if self.models[self.current_model_id].status != ModelStatus.RUNNING:
                    self.current_model_id = None
            logger.info(f"从 {self._state_file} 恢复了 {len(self.models)} 个模型状态")
        except Exception as e:
            logger.error(f"加载模型状态失败: {e}")

    def _schedule_save(self) -> None:
        self._dirty = True
        if self._debounce_timer is not None:
            self._debounce_timer.cancel()
        timer = threading.Timer(_DEBOUNCE_SECONDS, lambda: self._flush_state(timer))
        timer.daemon = True
        self._debounce_timer = timer
        timer.start()

    def _flush_state(self, _fired_timer: threading.Timer | None = None) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            if _fired_timer is None or self._debounce_timer is _fired_timer:
                self._debounce_timer = None
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                data = {
                    "models": {mid: m.model_dump(mode="json") for mid, m in self.models.items()},
                    "current_model_id": self.current_model_id,
                }
                tmp_file = self._state_file.with_name(self._state_file.name + ".tmp")
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                os.replace(tmp_file, self._state_file)
            except Exception as e:
                logger.error(f"保存模型状态失败: {e}")

    def add_model(self, model_id: str, name: str, path: str, config: dict | None = None, size_bytes: int = 0) -> ModelInfo:
        config = config or {}
        with self._lock:
            model_info = ModelInfo(
                id=model_id,
                name=name,
                path=path,
                status=ModelStatus.STOPPED,
                config=ModelConfig(**config),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                size_bytes=size_bytes
            )
            self.models[model_id] = model_info
            self._schedule_save()
        return model_info

    def update_model_status(self, model_id: str, status: ModelStatus,
                            error_message: str = "", *,
                            now: datetime | None = None) -> bool:
        _now = now or datetime.now()
        with self._lock:
            if model_id not in self.models:
                return False
            model = self.models[model_id]
            prev_status = model.status
            if status != prev_status and status not in _ALLOWED_TRANSITIONS.get(
                    prev_status, frozenset()):
                logger.warning(
                    f"Unexpected model status transition {prev_status.value} -> "
                    f"{status.value} for model {model_id} (not in "
                    f"_ALLOWED_TRANSITIONS; allowed by design, see QE-14)")
            model.status = status
            if status == ModelStatus.LOADING and prev_status != ModelStatus.LOADING:
                model.loading_since = _now
            elif status != ModelStatus.LOADING:
                model.loading_since = None
            model.updated_at = _now
            if error_message:
                model.error_message = error_message
            self._schedule_save()
            return True

    def update_model_size(self, model_id: str, size_bytes: int) -> bool:
        with self._lock:
            if model_id not in self.models:
                return False
            self.models[model_id].size_bytes = size_bytes
            self.models[model_id].updated_at = datetime.now()
            self._schedule_save()
            return True

    def update_model_config(self, model_id: str, config: dict) -> bool:
        with self._lock:
            if model_id not in self.models:
                return False
            current_config = self.models[model_id].config.model_dump()
            current_config.update(config)
            self.models[model_id].config = ModelConfig(**current_config)
            self.models[model_id].updated_at = datetime.now()
            self._schedule_save()
            return True

    def replace_model_config(self, model_id: str, config: dict) -> bool:
        with self._lock:
            if model_id not in self.models:
                return False
            self.models[model_id].config = ModelConfig(**config)
            self.models[model_id].updated_at = datetime.now()
            self._schedule_save()
            return True

    def set_current_model(self, model_id: str) -> bool:
        with self._lock:
            if model_id not in self.models:
                return False
            self.current_model_id = model_id
            self._schedule_save()
            return True

    def get_model(self, model_id: str) -> ModelInfo | None:
        with self._lock:
            return self.models.get(model_id)

    def get_all_models(self) -> list[ModelInfo]:
        with self._lock:
            return list(self.models.values())

    def clear_current_model(self, model_id: str) -> None:
        with self._lock:
            if self.current_model_id == model_id:
                self.current_model_id = None
                self._schedule_save()

    def remove_model(self, model_id: str) -> bool:
        with self._lock:
            if model_id not in self.models:
                return False
            if self.current_model_id == model_id:
                self.current_model_id = None
            del self.models[model_id]
            self._schedule_save()
            return True
