import logging
import shutil
import socket
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_bmc_diag_lock = threading.Lock()
_bmc_diag_last: tuple[bool, bool] | None = None
_bmc_diag_last_ts = 0.0

def _bmc_diag(host: str, pre: str, pre_ms: float, budget: float,
              ipmi: str, ipmi_ms: float, connected: bool, power: Any) -> None:
    global _bmc_diag_last, _bmc_diag_last_ts
    key = (connected, pre == "timeout")
    now = time.monotonic()
    with _bmc_diag_lock:
        if (_bmc_diag_last is not None and _bmc_diag_last == key
                and now - _bmc_diag_last_ts < 15.0):
            return
        _bmc_diag_last = key
        _bmc_diag_last_ts = now
    logger.info(
        "BMC probe diag: host=%s 443=%s(%.0fms) ipmi=%s(budget=%gs, %.0fms) "
        "connected=%s power=%s",
        host, pre, pre_ms, ipmi, budget, ipmi_ms, connected, power)

class PowerOps:

    BMC_POWER_TIMEOUT = 20

    def power_on_server(self) -> dict[str, Any]:
        from ...config.remote_client import config_manager
        config = config_manager.get_config()

        if not config.bmc_host:
            return {"success": False, "error": "BMC host not configured (Server Config: BMC Address)"}
        if not config.bmc_password:
            return {"success": False, "error": "BMC password not configured (Server Config: BMC Password)"}

        ipmitool_path = shutil.which("ipmitool")
        if not ipmitool_path:
            return {"success": False, "error": "ipmitool not available on dashboard host"}

        cmd = [
            ipmitool_path, "-I", "lanplus",
            "-H", config.bmc_host,
            "-U", config.bmc_username or "admin",
            "-P", config.bmc_password,
            "chassis", "power", "on",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=self.BMC_POWER_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "BMC power-on timeout (no response from BMC)"}
        except Exception as e:
            return {"success": False, "error": f"BMC power-on failed: {e}"}

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            detail = ("Cannot reach BMC or BMC authentication failed "
                      "(check BMC address and credentials)"
                      if "unable to establish ipmi" in err.lower()
                      else (err[:200] or f"ipmitool exited with code {result.returncode}"))
            logger.warning("BMC power-on failed for %s (rc=%s)", config.bmc_host, result.returncode)
            return {"success": False, "error": detail}
        return {"success": True, "error": None}

    BMC_STATUS_TIMEOUT = 8
    BMC_STATUS_TIMEOUT_FAST = 3
    BMC_REACH_PROBE_TIMEOUT = 2
    BMC_WEB_PORT = 443

    def check_bmc_status(self) -> dict[str, Any]:
        from ...config.remote_client import config_manager
        config = config_manager.get_config()

        if not config.bmc_host:
            return {"configured": False, "connected": False, "power": None, "error": None}
        if not config.bmc_password:
            return {"configured": True, "connected": False, "power": None,
                    "error": "BMC password not configured (Server Config: BMC Password)"}

        ipmitool_path = shutil.which("ipmitool")
        if not ipmitool_path:
            return {"configured": True, "connected": False, "power": None,
                    "error": "ipmitool not available on dashboard host"}

        t_pre = time.monotonic()
        pre_probe_timeout = False
        pre_probe_rst = False
        try:
            with socket.create_connection(
                    (config.bmc_host, self.BMC_WEB_PORT),
                    timeout=self.BMC_REACH_PROBE_TIMEOUT):
                pass
        except ConnectionRefusedError:
            pre_probe_rst = True
        except OSError:
            pre_probe_timeout = True
        pre_ms = (time.monotonic() - t_pre) * 1000
        pre = ("timeout" if pre_probe_timeout
               else "rst" if pre_probe_rst else "ok")

        ipmi_timeout = (self.BMC_STATUS_TIMEOUT_FAST if pre_probe_timeout
                        else self.BMC_STATUS_TIMEOUT)
        cmd = [
            ipmitool_path, "-I", "lanplus",
            "-H", config.bmc_host,
            "-U", config.bmc_username or "admin",
            "-P", config.bmc_password,
            "chassis", "power", "status",
        ]
        t_ipmi = time.monotonic()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=ipmi_timeout)
        except subprocess.TimeoutExpired:
            _bmc_diag(config.bmc_host, pre, pre_ms, ipmi_timeout, "timeout",
                      (time.monotonic() - t_ipmi) * 1000, False, None)
            return {"configured": True, "connected": False, "power": None,
                    "error": "BMC status probe timeout (no response from BMC)"}
        except Exception as e:
            _bmc_diag(config.bmc_host, pre, pre_ms, ipmi_timeout, "fail",
                      (time.monotonic() - t_ipmi) * 1000, False, None)
            return {"configured": True, "connected": False, "power": None,
                    "error": f"BMC status probe failed: {e}"}
        ipmi_ms = (time.monotonic() - t_ipmi) * 1000

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            detail = ("Cannot reach BMC or BMC authentication failed "
                      "(check BMC address and credentials)"
                      if "unable to establish ipmi" in err.lower()
                      else (err[:200] or f"ipmitool exited with code {result.returncode}"))
            logger.debug("BMC status probe failed for %s (rc=%s)", config.bmc_host, result.returncode)
            _bmc_diag(config.bmc_host, pre, pre_ms, ipmi_timeout,
                      f"fail({result.returncode})", ipmi_ms, False, None)
            return {"configured": True, "connected": False, "power": None, "error": detail}

        out = (result.stdout or "").lower()
        power = "on" if "power is on" in out else ("off" if "power is off" in out else None)
        _bmc_diag(config.bmc_host, pre, pre_ms, ipmi_timeout, "ok", ipmi_ms, True, power)
        return {"configured": True, "connected": True, "power": power, "error": None}
