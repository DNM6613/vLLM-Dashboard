import hashlib
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

class ModelLaunchConfigManager:

    def __init__(self, base_dir: str | None = None):
        self.base_dir = (
            Path(base_dir) if base_dir
            else Path(__file__).resolve().parent.parent.parent / "data" / "model_launch_configs"
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def _safe_id(model_id: str) -> str:
        return model_id.replace("/", "_").replace("\\", "_")

    def _get_config_path(self, model_id: str) -> Path:
        return self.base_dir / f"{self._safe_id(model_id)}.json"

    def _disambiguated_path(self, model_id: str) -> Path:
        digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:8]
        return self.base_dir / f"{self._safe_id(model_id)}--{digest}.json"

    def _read_stored(self, path: Path) -> dict | None:
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def save_config(self, model_id: str, config: dict) -> bool:
        with self._lock:
            return self._save_config_locked(model_id, config)

    def _save_config_locked(self, model_id: str, config: dict) -> bool:
        try:
            config_path = self._get_config_path(model_id)
            existing = self._read_stored(config_path)
            if existing is not None and existing.get("_model_id") not in (None, model_id):
                config_path = self._disambiguated_path(model_id)
            config_with_id = {"_model_id": model_id, **config}
            tmp_path = config_path.with_name(config_path.name + ".tmp")
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(config_with_id, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, config_path)
            return True
        except Exception as e:
            logger.error(f"保存模型配置失败: {e}")
            return False

    def _candidate_paths(self, model_id: str) -> tuple[Path, Path]:
        return self._get_config_path(model_id), self._disambiguated_path(model_id)

    def load_config(self, model_id: str) -> dict | None:
        for config_path in self._candidate_paths(model_id):
            if not config_path.exists():
                continue
            config = self._read_stored(config_path)
            if config is None:
                continue
            stored_id = config.get("_model_id")
            if stored_id is not None and stored_id != model_id:
                continue
            return config
        return None

    def delete_config(self, model_id: str) -> bool:
        with self._lock:
            try:
                for config_path in self._candidate_paths(model_id):
                    if not config_path.exists():
                        continue
                    config = self._read_stored(config_path)
                    if config is None:
                        continue
                    stored_id = config.get("_model_id")
                    if stored_id is not None and stored_id != model_id:
                        continue
                    config_path.unlink()
                return True
            except Exception as e:
                logger.error(f"删除模型配置失败: {e}")
                return False

launch_config_manager = ModelLaunchConfigManager()
