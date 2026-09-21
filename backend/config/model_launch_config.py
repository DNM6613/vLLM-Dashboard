import hashlib
import json
import logging
import os
import re
import shlex
import threading
from pathlib import Path

from ..controller.state_machine import state_machine
from ..schemas.model import ModelStatus

logger = logging.getLogger(__name__)

_ENV_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

def strip_shell_comment(line: str) -> str:
    """Drop a shell comment (``#`` at word start) while honoring quotes.

    shlex.split does not treat ``#`` as a comment, so a trailing
    ``# comment`` would otherwise fold into the value. A ``#`` only starts
    a comment when it is at the beginning of the line or preceded by
    whitespace, and never inside single/double quotes.
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1].isspace():
                return line[:i].rstrip()
    return line

def parse_env_vars(env_vars: str) -> list[tuple[str, str]]:
    parsed: list[list[str]] = []
    for raw_line in env_vars.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = strip_shell_comment(line)
        if not line:
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            raise ValueError(f"unparseable env line: {line!r}")
        tokens = [t for t in tokens if t != "export"]
        current: int | None = None
        for token in tokens:
            if "=" in token:
                key, _sep, value = token.partition("=")
                if not _ENV_KEY_RE.match(key):
                    raise ValueError(f"expected KEY=VALUE: {line!r}")
                parsed.append([key, value])
                current = len(parsed) - 1
            else:
                if current is None:
                    raise ValueError(f"expected KEY=VALUE: {line!r}")
                prev_val = parsed[current][1]
                parsed[current][1] = f"{prev_val} {token}".strip() if prev_val else token
    return [(k, v) for k, v in parsed]

def _to_port(value: str) -> int | None:
    try:
        port = int(str(value).strip())
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None

def _first_api_key(value: str) -> str | None:
    for part in value.split(","):
        part = part.strip()
        if part:
            return part
    return None

def parse_launch_api(start_command: str, env_vars: str) -> tuple[int | None, str | None]:
    """Extract the vLLM API port and API key from a model launch config.

    Scans the start command for ``--port`` / ``--api-key`` / ``--api-keys``
    flags and falls back to environment variables: ``PORT`` for the port,
    ``VLLM_API_KEY`` then ``API_KEY`` for the key (vLLM itself reads
    ``VLLM_API_KEY`` when no ``--api-key`` flag is given). Command flags
    take precedence. Returns ``(port, api_key)`` — either may be ``None``
    when not specified.
    """
    port: int | None = None
    api_key: str | None = None

    try:
        tokens = shlex.split(start_command or "")
    except ValueError:
        tokens = (start_command or "").split()

    i = 0
    while i < len(tokens):
        flag = tokens[i]
        if flag == "--port":
            if i + 1 < len(tokens):
                port = _to_port(tokens[i + 1])
                i += 1
        elif flag.startswith("--port="):
            port = _to_port(flag.partition("=")[2])
        elif flag == "--api-key":
            if i + 1 < len(tokens):
                api_key = tokens[i + 1] or None
                i += 1
        elif flag.startswith("--api-key="):
            api_key = flag.partition("=")[2] or None
        elif flag == "--api-keys":
            if i + 1 < len(tokens):
                api_key = _first_api_key(tokens[i + 1])
                i += 1
        elif flag.startswith("--api-keys="):
            api_key = _first_api_key(flag.partition("=")[2])
        i += 1

    if port is None or api_key is None:
        try:
            env_pairs = parse_env_vars(env_vars or "")
        except ValueError:
            env_pairs = []
        vllm_api_key: str | None = None
        generic_api_key: str | None = None
        for key, value in env_pairs:
            if key == "PORT" and port is None:
                port = _to_port(value)
            elif key == "VLLM_API_KEY" and vllm_api_key is None:
                vllm_api_key = value or None
            elif key == "API_KEY" and generic_api_key is None:
                generic_api_key = value or None
        if api_key is None:
            api_key = vllm_api_key or generic_api_key

    return port, api_key

def resolve_launch_api() -> tuple[int | None, str | None]:
    """Resolve the ``(port, api_key)`` of the vLLM API from the launch config
    of the model currently being served.

    Candidates, in order: the current model, then any LOADING model (an
    in-flight start the backend must probe to detect), then any RUNNING
    model. The first candidate whose launch config yields a port or key
    wins. Returns ``(None, None)`` when nothing yields a value — callers
    fall back to the stored server config.

    When no model is tracked as serving, the current model (any status) and
    then any other model are still tried: otherwise a stale ``stopped``
    state (e.g. vLLM started outside the dashboard) means the API key is
    never resolved, every probe 401s, and the status sync can never flip
    the state back to running.
    """
    models = state_machine.get_all_models()
    # Only trust the current model id if that model is actually serving; a
    # stale id (e.g. the model stopped but the id was not cleared) must not
    # shadow the model that is really serving.
    current_id = state_machine.current_model_id
    current_serving = any(
        m.id == current_id and m.status in (ModelStatus.RUNNING, ModelStatus.LOADING)
        for m in models
    )
    candidates: list[str] = []
    if current_serving:
        candidates.append(current_id)
    for status in (ModelStatus.LOADING, ModelStatus.RUNNING):
        for model in models:
            if model.id not in candidates and model.status == status:
                candidates.append(model.id)
    if not candidates:
        if current_id:
            candidates.append(current_id)
        for model in models:
            if model.id not in candidates:
                candidates.append(model.id)
    for model_id in candidates:
        config = launch_config_manager.load_config(model_id)
        if not config:
            continue
        port, api_key = parse_launch_api(
            config.get("start_command", ""), config.get("env_vars", "")
        )
        if port is not None or api_key is not None:
            return port, api_key
    return None, None

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
