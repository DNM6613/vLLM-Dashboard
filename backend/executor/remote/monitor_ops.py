import logging
import shlex
import threading
from typing import Any

logger = logging.getLogger(__name__)

def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False

_cpu_prev_stat: dict[tuple[str, int, str], str] = {}
_cpu_sample_lock = threading.Lock()

def _safe_int(s: str, default: int = 0) -> int:
    s = s.strip()
    return int(s) if s and s != "N/A" else default

def _safe_float(s: str, default: float = 0.0) -> float:
    s = s.strip()
    return float(s) if s and s != "N/A" else default

_NVSMI_CMD = (
    "nvidia-smi --query-gpu=index,name,memory.total,memory.used,"
    "memory.free,temperature.gpu,power.draw,power.limit,"
    "utilization.gpu,utilization.memory --format=csv,noheader,nounits"
)
_SYSINFO_MARKER = "===VDB_SYSINFO==="

_AWK_CPU_DIFF = (
    "BEGIN{n1=split(a,x,\" \");n2=split(b,y,\" \");"
    "t1=0;t2=0;for(i=2;i<=n1;i++)t1+=x[i];for(i=2;i<=n2;i++)t2+=y[i];"
    "dt=t2-t1;di=(y[5]+y[6])-(x[5]+x[6]);"
    "if(dt>0)printf \"%.2f\",100*(dt-di)/dt;else print \"0.00\"}"
)
_MEM_DISK_CMD = (
    "MT=$(grep -m1 '^MemTotal:' /proc/meminfo | cut -d: -f2 | tr -d ' kB'); "
    "MA=$(grep -m1 '^MemAvailable:' /proc/meminfo | cut -d: -f2 | tr -d ' kB'); "
    "MF=$(grep -m1 '^MemFree:' /proc/meminfo | cut -d: -f2 | tr -d ' kB'); "
    "echo \"$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ //')|"
    "$(nproc)|${CPU:-0.00}|"
    "$((MT*1024)),$(((MT-MA)*1024)),$((MF*1024))|"
    "$(df -B 1 / | awk 'NR==2 {print $2\",\"$3\",\"$4}')|"
)

def _parse_nvidia_smi_lines(stdout: str) -> list[dict]:
    gpus = []
    for line in stdout.strip().split("\n"):
        if line.strip():
            parts = line.split(",")
            if len(parts) >= 10:
                try:
                    gpus.append({
                        "index": _safe_int(parts[0]),
                        "name": parts[1].strip() or "Unknown GPU",
                        "memory_total": _safe_int(parts[2]) * 1024 * 1024,
                        "memory_used": _safe_int(parts[3]) * 1024 * 1024,
                        "memory_free": _safe_int(parts[4]) * 1024 * 1024,
                        "temperature": _safe_int(parts[5]),
                        "power_draw": _safe_float(parts[6]),
                        "power_limit": _safe_float(parts[7]),
                        "utilization_gpu": _safe_int(parts[8]),
                        "utilization_memory": _safe_int(parts[9])
                    })
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse nvidia-smi line: {line} ({e})")
    return gpus

class MonitorOps:

    def get_nvidia_smi(self) -> dict[str, Any]:
        result = self.execute(_NVSMI_CMD)

        if not result["success"]:
            return {"gpus": [], "error": result["stderr"]}

        return {"gpus": _parse_nvidia_smi_lines(result["stdout"]), "error": None}

    def get_system_info_combined(self) -> dict[str, Any]:
        result = self.execute(self._build_sysinfo_cmd())
        if not result["success"]:
            return {"cpu": None, "memory": None, "disk": None}
        return self._parse_sysinfo(result["stdout"])

    def _build_sysinfo_cmd(self) -> str:
        _cpu_key = (self.host, self.port, self.username)
        with _cpu_sample_lock:
            _prev = _cpu_prev_stat.get(_cpu_key)
        if _prev is not None:
            return (
                f"PCS={shlex.quote(_prev)}; "
                "CS=$(head -1 /proc/stat); "
                f"CPU=$(awk -v a=\"$PCS\" -v b=\"$CS\" '{_AWK_CPU_DIFF}'); "
                + _MEM_DISK_CMD + "$CS\""
            )
        return (
            "CS1=$(head -1 /proc/stat); sleep 1; CS2=$(head -1 /proc/stat); "
            f"CPU=$(awk -v a=\"$CS1\" -v b=\"$CS2\" '{_AWK_CPU_DIFF}'); "
            + _MEM_DISK_CMD + "$CS2\""
        )

    def _parse_sysinfo(self, line: str) -> dict[str, Any]:
        _cpu_key = (self.host, self.port, self.username)
        try:
            parts = line.strip().split("|")
            cpu_model = (parts[0] if len(parts) > 0 else "") or "Unknown"
            cpu_count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            cpu_usage = round(float(parts[2]), 2) if len(parts) > 2 and _is_float(parts[2]) else 0.0

            if len(parts) > 5 and parts[5].strip():
                with _cpu_sample_lock:
                    _cpu_prev_stat[_cpu_key] = parts[5].strip()

            mem_parts = parts[3].split(",") if len(parts) > 3 else []
            memory = None
            if len(mem_parts) >= 3 and all(p.isdigit() for p in mem_parts[:3]):
                mem_total, mem_used, mem_free = (int(p) for p in mem_parts[:3])
                memory = {
                    "total": mem_total, "used": mem_used, "free": mem_free,
                    "usage": round((mem_used / mem_total) * 100, 2) if mem_total > 0 else 0.0,
                }

            disk_parts = parts[4].split(",") if len(parts) > 4 else []
            disk = None
            if len(disk_parts) >= 3 and all(p.isdigit() for p in disk_parts[:3]):
                disk_total, disk_used, disk_free = (int(p) for p in disk_parts[:3])
                disk = {
                    "total": disk_total, "used": disk_used, "free": disk_free,
                    "usage": round((disk_used / disk_total) * 100, 2) if disk_total > 0 else 0.0,
                }
        except Exception:
            return {"cpu": None, "memory": None, "disk": None}

        return {
            "cpu": {"model": cpu_model, "count": cpu_count, "usage": cpu_usage},
            "memory": memory,
            "disk": disk,
        }

    def get_hardware_snapshot(self) -> dict[str, Any]:
        cmd = (
            f"{_NVSMI_CMD}; "
            f"echo '{_SYSINFO_MARKER}'; "
            + self._build_sysinfo_cmd()
        )
        result = self.execute(cmd)
        if not result["success"]:
            return {
                "gpus": [], "cpu": None, "memory": None, "disk": None,
                "error": result["stderr"],
            }
        lines = result["stdout"].splitlines()
        idx = next(
            (i for i, ln in enumerate(lines) if ln.strip() == _SYSINFO_MARKER),
            None)
        if idx is None:
            return {
                "gpus": _parse_nvidia_smi_lines(result["stdout"]),
                "cpu": None, "memory": None, "disk": None,
                "error": result["stderr"] or None,
            }
        gpus = _parse_nvidia_smi_lines("\n".join(lines[:idx]))
        sys_lines = [ln for ln in lines[idx + 1:] if ln.strip()]
        sysinfo = (
            self._parse_sysinfo(sys_lines[0])
            if sys_lines else {"cpu": None, "memory": None, "disk": None}
        )
        return {
            "gpus": gpus,
            "cpu": sysinfo["cpu"],
            "memory": sysinfo["memory"],
            "disk": sysinfo["disk"],
            "error": result["stderr"] if not gpus else None,
        }

    def get_software_info(self) -> dict[str, Any]:
        activate = self._get_activate_cmd()
        cmd = (
            "OS=$(grep -m1 '^PRETTY_NAME=' /etc/os-release | cut -d'\"' -f2); "
            "DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1); "
            "TOOLKIT=$(/usr/local/cuda/bin/nvcc --version 2>/dev/null | grep -oE 'release [0-9]+\\.[0-9]+' | head -1 | awk '{print $2}'); "
            "[ -n \"$TOOLKIT\" ] || TOOLKIT=$(readlink -f /usr/local/cuda 2>/dev/null | sed -n 's|.*cuda-\\([0-9][0-9.]*\\)$|\\1|p'); "
            "VLLM=$( { " + activate + "python -c 'import vllm;print(vllm.__version__)' ; } 2>/dev/null); "
            "echo \"$OS|$DRV|$TOOLKIT|$VLLM\""
        )
        result = self.execute(cmd, timeout=30)
        if not result["success"]:
            return {
                "os": "Unknown", "gpu_driver": "Unknown",
                "cuda_toolkit": "Unknown", "vllm": "Unknown",
            }

        parts = result["stdout"].strip().split("|")

        def _at(i: int) -> str:
            return parts[i].strip() if i < len(parts) else ""

        return {
            "os": _at(0) or "Unknown",
            "gpu_driver": _at(1) or "Unknown",
            "cuda_toolkit": _at(2) or "Unknown",
            "vllm": _at(3) or "Unknown",
        }
