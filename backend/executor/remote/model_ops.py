import logging
import re
import shlex
from typing import Any

from ...config.server_config import has_parent_path_segment
from ...config.settings import settings

logger = logging.getLogger(__name__)

HF_HUB_SCAN_ROOT = "$HOME/.cache/huggingface/hub"

_SCAN_EXCLUDE = (
    "-not -path '*/node_modules/*' -not -path '*/.git/*' "
    "-not -path '*/.npm/*' -not -path '*/.local/lib/*' "
    "-not -path '*/.cache/pip/*' -not -path '*/.cache/uv/*'"
)

def _expand_scan_root(path: str) -> str | None:
    if not path or has_parent_path_segment(path):
        return None
    if path == "~":
        return "$HOME"
    if path.startswith("/"):
        return shlex.quote(path)
    if path.startswith("~") and not path.startswith("~/"):
        return None
    rest = path[2:] if path.startswith("~/") else path
    return f"$HOME/{shlex.quote(rest)}"

def _build_default_scan_command(custom_dir: str = "") -> str:
    roots = [HF_HUB_SCAN_ROOT]
    custom = _expand_scan_root(custom_dir)
    if custom and custom != HF_HUB_SCAN_ROOT:
        roots.append(custom)
    parts = [
        f"[ -d {root} ] && find {root} -maxdepth 8 -name 'config.json' "
        f"{_SCAN_EXCLUDE} 2>/dev/null"
        for root in roots
    ]
    return "{ " + " ; ".join(parts) + " ; } | head -20"

class ModelOps:

    def scan_models(self, model_dir: str = "", custom_dir: str = "") -> dict[str, Any]:
        if model_dir and has_parent_path_segment(model_dir):
            return {"success": False, "models": [], "error": "Path traversal not allowed", "count": 0}

        if model_dir:
            if model_dir.startswith("~/"):
                _rel = model_dir[2:]
            elif model_dir == "~":
                _rel = ""
            elif not model_dir.startswith("/"):
                _rel = model_dir
            else:
                _rel = ""
            if not re.fullmatch(r'[\w./\-:@~]*', _rel):
                return {"success": False, "models": [], "error": "Invalid model_dir", "count": 0}
            if model_dir.startswith("~/"):
                scan_root = f"$HOME/{model_dir[2:]}"
            elif model_dir == "~":
                scan_root = "$HOME"
            elif model_dir.startswith("/"):
                scan_root = "~"
            else:
                scan_root = f"$HOME/{model_dir}"
            scan_command = (
                f"find {scan_root} -maxdepth 8 -name 'config.json' "
                f"{_SCAN_EXCLUDE} 2>/dev/null | head -20"
            )
        else:
            scan_command = _build_default_scan_command(custom_dir)

        result = self.execute(scan_command, timeout=140)
        if not result["success"]:
            return {"success": False, "models": [], "error": "Scan failed", "count": 0}
        if not result["stdout"].strip():
            return {"success": True, "models": [], "count": 0}

        unique_models: dict[str, str] = {}
        for line in result["stdout"].strip().split("\n"):
            config_path = line.strip()
            if not config_path:
                continue
            snapshot_path = config_path.rsplit("/", 1)[0]

            model_path = snapshot_path
            if "/snapshots/" in snapshot_path:
                model_path = snapshot_path.split("/snapshots/")[0]

            if model_path not in unique_models:
                unique_models[model_path] = snapshot_path

        names: dict[str, str] = {}
        if unique_models:
            name_parts: list[str] = []
            for model_path, snapshot_path in unique_models.items():
                _q_model = shlex.quote(model_path)
                _q_config = shlex.quote(f"{snapshot_path}/config.json")
                name_parts.append(
                    "printf '%s\\t%s\\n' "
                    f"{_q_model} \"$(cat {_q_config} 2>/dev/null | "
                    "grep -o '\"_name_or_path\"[[:space:]]*:[[:space:]]*\"[^\"]*\"' "
                    "| head -1 | sed 's/.*\"\\([^\"]*\\)\"$/\\1/')\""
                )
            name_result = self.execute(" ; ".join(name_parts))
            if name_result["success"]:
                for _line in name_result["stdout"].splitlines():
                    _p, _sep, _n = _line.partition("\t")
                    if _n.strip():
                        names[_p] = _n.strip()

        candidates: list[dict[str, Any]] = []
        for model_path, snapshot_path in unique_models.items():
            name = names.get(model_path) or self._infer_model_name_from_path(model_path)
            if name:
                dirname = model_path.rsplit("/", 1)[-1]
                if "." in dirname:
                    safe_dirname = dirname.replace(".", "_")
                    parent_dir = model_path.rsplit("/", 1)[0]
                    symlink_path = f"{parent_dir}/{safe_dirname}"
                    self.execute(
                        f"ln -sfn {shlex.quote(snapshot_path)} {shlex.quote(symlink_path)} 2>/dev/null"
                    )
                    model_path = symlink_path

                candidates.append({"name": name, "path": model_path})

        if candidates:
            quoted = " ".join(shlex.quote(c["path"]) for c in candidates)
            size_cmd = f"du -sbL {quoted} 2>/dev/null"
            size_result = self.execute(size_cmd, timeout=140)
            sizes: dict[str, int] = {}
            if size_result["success"]:
                for sline in size_result["stdout"].splitlines():
                    size_str, _sep, path = sline.partition("\t")
                    if size_str.strip().isdigit() and path:
                        sizes[path] = int(size_str.strip())
            for c in candidates:
                c["size_bytes"] = sizes.get(c["path"], 0)

        return {
            "success": True,
            "models": candidates,
            "count": len(candidates)
        }

    def check_models_existence(self, paths: list[str]) -> dict[str, bool]:
        if not paths:
            return {}
        parts = []
        for p in paths:
            q = shlex.quote(p)
            parts.append(f"(test -d {q} && printf '1\\t%s\\n' {q} || printf '0\\t%s\\n' {q})")
        cmd = " ; ".join(parts)
        result = self.execute(cmd)
        if not result["success"]:
            return {}
        existence: dict[str, bool] = {}
        for line in result["stdout"].splitlines():
            flag, _sep, path = line.partition("\t")
            if path in paths:
                existence[path] = flag == "1"
        return existence

    def _resolve_model_name(self, snapshot_path: str, base_path: str = "") -> str | None:
        safe_config_path = shlex.quote(f"{snapshot_path}/config.json")
        config_cmd = f"cat {safe_config_path} 2>/dev/null | grep -o '\"_name_or_path\"[[:space:]]*:[[:space:]]*\"[^\"]*\"' | head -1 | sed 's/.*\"\\([^\"]*\\)\"$/\\1/'"
        try:
            result = self.execute(config_cmd)
            if result["success"] and result["stdout"].strip():
                return result["stdout"].strip()
        except Exception:
            pass

        return self._infer_model_name_from_path(base_path or snapshot_path)

    @staticmethod
    def _infer_model_name_from_path(infer_path: str) -> str | None:
        parts = infer_path.rstrip("/").split("/")
        if len(parts) >= 2:
            dirname = parts[-1]
            if dirname.startswith("models--"):
                name_parts = dirname.split("--", 2)
                if len(name_parts) >= 3:
                    return f"{name_parts[1]}/{name_parts[2]}"
            return f"{parts[-2]}/{dirname}"

        return None

    def download_model(self, model_repo: str, model_save_path: str = "", hf_mirror: bool = True) -> dict[str, Any]:
        if model_save_path:
            if has_parent_path_segment(model_save_path):
                raise ValueError("Path traversal is not allowed")

            home_result = self.execute("echo $HOME")
            if not home_result["success"]:
                raise ValueError("Failed to resolve remote HOME directory")
            home_dir = home_result["stdout"].strip()
            if not home_dir:
                raise ValueError("Failed to resolve remote HOME directory")
            if model_save_path.startswith("~/"):
                model_save_path = home_dir + model_save_path[1:]
            elif model_save_path == "~":
                model_save_path = home_dir
            elif not model_save_path.startswith("/"):
                model_save_path = f"{home_dir}/{model_save_path}"
            if not model_save_path.startswith(home_dir + "/") and model_save_path != home_dir:
                raise ValueError("Model save path must be under home directory")

        log_file = f"/tmp/model_download_{model_repo.replace('/', '_')}.log"

        safe_repo = shlex.quote(model_repo)
        if model_save_path:
            download_cmd = f"hf download {safe_repo} --local-dir {shlex.quote(model_save_path)}"
        else:
            download_cmd = f"hf download {safe_repo}"

        activate_cmd = self._get_activate_cmd()

        env_prefix = ""
        if hf_mirror and settings.HF_ENDPOINT:
            env_prefix = (
                f"export HF_ENDPOINT={shlex.quote(settings.HF_ENDPOINT)} && "
                "export HF_HUB_DISABLE_XET=1 && "
            )

        cmd = f"nohup bash -c \"{self._escape_double_quoted(env_prefix + activate_cmd + download_cmd)}\" > {shlex.quote(log_file)} 2>&1 & echo $!"

        result = self.execute(cmd)

        return {
            "success": result["success"] and result["stdout"].strip().isdigit(),
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "log_file": log_file
        }
