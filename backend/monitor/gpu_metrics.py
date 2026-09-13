from typing import Any


def map_nvidia_smi_gpus(raw_gpus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gpus = []
    for gpu in raw_gpus:
        gpus.append({
            "index": gpu.get("index", 0),
            "name": gpu.get("name", "Unknown"),
            "memory_used_mb": gpu.get("memory_used", 0) // (1024 * 1024),
            "memory_total_mb": gpu.get("memory_total", 0) // (1024 * 1024),
            "temperature_c": gpu.get("temperature", 0),
            "power_draw_w": int(gpu.get("power_draw", 0)),
            "power_limit_w": int(gpu.get("power_limit", 0)),
            "utilization_gpu": gpu.get("utilization_gpu", 0),
            "utilization_memory": gpu.get("utilization_memory", 0),
        })
    return gpus
