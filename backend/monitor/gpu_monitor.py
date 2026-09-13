import atexit
import contextlib
import threading
import time

from ..schemas.hardware import GPUInfo

try:
    import pynvml
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False

class GPUMonitor:
    def __init__(self):
        self.gpu_info: list[GPUInfo] = []
        self._nvml_initialized = False
        self._atexit_registered = False

        if NVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self._nvml_initialized = True
            except Exception:
                self._nvml_initialized = False

        self.running = False
        self.update_interval = 1.0
        self.thread: threading.Thread | None = None

    def get_gpu_count(self) -> int:
        if not self._nvml_initialized:
            return 0
        try:
            return pynvml.nvmlDeviceGetCount()
        except Exception:
            return 0

    def get_gpu_info(self, index: int) -> GPUInfo:
        if not self._nvml_initialized:
            return GPUInfo(
                index=index,
                name="Unknown",
                memory_used_mb=0,
                memory_total_mb=0,
                temperature_c=0,
                power_draw_w=0,
                power_limit_w=0,
                utilization_gpu=0,
                utilization_memory=0
            )

        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = pynvml.nvmlDeviceGetName(handle)
            name = name.decode("utf-8") if isinstance(name, bytes) else name
            memory_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)

            try:
                power_usage = pynvml.nvmlDeviceGetPowerUsage(handle) // 1000
            except Exception:
                power_usage = 0

            try:
                power_limit = pynvml.nvmlDeviceGetEnforcedPowerLimit(handle) // 1000
            except Exception:
                power_limit = 0

            try:
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            except Exception:
                utilization = None

            return GPUInfo(
                index=index,
                name=name,
                memory_used_mb=memory_info.used // (1024 * 1024),
                memory_total_mb=memory_info.total // (1024 * 1024),
                temperature_c=temperature,
                power_draw_w=power_usage,
                power_limit_w=power_limit,
                utilization_gpu=utilization.gpu if utilization else 0,
                utilization_memory=utilization.memory if utilization else 0
            )
        except Exception as e:
            return GPUInfo(
                index=index,
                name=f"Error: {e}",
                memory_used_mb=0,
                memory_total_mb=0,
                temperature_c=0,
                power_draw_w=0,
                power_limit_w=0,
                utilization_gpu=0,
                utilization_memory=0
            )

    def update_all_gpus(self) -> None:
        self.gpu_info = [self.get_gpu_info(i) for i in range(self.get_gpu_count())]

    def get_current_metrics(self) -> list[dict]:
        self.update_all_gpus()
        return [
            {
                "index": gpu.index,
                "name": gpu.name,
                "memory_used_mb": gpu.memory_used_mb,
                "memory_total_mb": gpu.memory_total_mb,
                "temperature_c": gpu.temperature_c,
                "power_draw_w": gpu.power_draw_w,
                "power_limit_w": gpu.power_limit_w,
                "utilization_gpu": gpu.utilization_gpu,
                "utilization_memory": gpu.utilization_memory
            }
            for gpu in self.gpu_info
        ]

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        if NVML_AVAILABLE and not self._nvml_initialized:
            try:
                pynvml.nvmlInit()
                self._nvml_initialized = True
            except Exception:
                self._nvml_initialized = False
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                import logging
                logging.getLogger(__name__).warning("GPUMonitor thread did not stop within 5s; forcing shutdown")
        if self._nvml_initialized:
            with contextlib.suppress(Exception):
                pynvml.nvmlShutdown()
            self._nvml_initialized = False

    def _run(self) -> None:
        while self.running:
            try:
                self.update_all_gpus()
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception("GPU monitoring update failed: %s", e)
            time.sleep(self.update_interval)
