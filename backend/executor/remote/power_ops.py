import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

def _ipmitool_env(password: str) -> dict:
    """Subprocess env for ipmitool's -E flag: the password is read from
    IPMI_PASSWORD instead of the -P command line, so it does not appear
    in the local process list."""
    env = dict(os.environ)
    env["IPMI_PASSWORD"] = password
    return env

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
    # Power-on verification/retry budget. Right after a clean power-off the
    # BMC can briefly refuse a power-on, and a single IPMI command can be
    # dropped. `chassis power on` is idempotent, so we verify the chassis
    # actually reaches "on" and re-issue as needed until it does.
    #
    # Verification is two-channel: BMC status AND a network/SSH probe of the
    # target itself. The BMC is often flaky or unreachable from the dashboard
    # host; the machine's own network coming up is the ground truth that it
    # booted, so a BMC that never confirms must not turn a real power-on into
    # a failure.
    POWERON_TOTAL_TIMEOUT = 120
    BMC_POWERON_RETRY_INTERVAL = 8
    BMC_POWERON_CONFIRM_SECS = 4
    POWERON_SSH_PROBE_INTERVAL = 5

    def _ipmi_power_on(self, config, ipmitool_path) -> tuple[bool, str | None]:
        cmd = [
            ipmitool_path, "-I", "lanplus",
            "-H", config.bmc_host,
            "-U", config.bmc_username or "admin",
            "-E",
            "chassis", "power", "on",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=self.BMC_POWER_TIMEOUT,
                                    env=_ipmitool_env(config.bmc_password))
        except subprocess.TimeoutExpired:
            return False, "BMC power-on timeout (no response from BMC)"
        except Exception as e:
            return False, f"BMC power-on failed: {e}"

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            detail = ("Cannot reach BMC or BMC authentication failed "
                      "(check BMC address and credentials)"
                      if "unable to establish ipmi" in err.lower()
                      else (err[:200] or f"ipmitool exited with code {result.returncode}"))
            logger.warning("BMC power-on failed for %s (rc=%s)", config.bmc_host, result.returncode)
            return False, detail
        return True, None

    def _confirm_power_on(self) -> bool:
        """Confirm the chassis stays 'on' for a short window.

        Filters a transient read and a power-off that is still completing (a
        clean poweroff takes a few seconds for the chassis to actually drop,
        so a single "on" read right after issuing power-on is not reliable).
        """
        end = time.monotonic() + self.BMC_POWERON_CONFIRM_SECS
        while time.monotonic() < end:
            time.sleep(1.5)
            if self.check_bmc_status().get("power") != "on":
                return False
        return True

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

        from .ssh_probe import check_ssh_connectivity

        deadline = time.monotonic() + self.POWERON_TOTAL_TIMEOUT
        last_issue = -1e9
        last_bmc = -1e9
        last_ssh = -1e9
        last_error: str | None = None
        last_ssh_error: str | None = None
        while time.monotonic() < deadline:
            if time.monotonic() - last_issue >= self.BMC_POWERON_RETRY_INTERVAL:
                ok, err = self._ipmi_power_on(config, ipmitool_path)
                last_issue = time.monotonic()
                if not ok:
                    last_error = err
            if time.monotonic() - last_bmc >= 3:
                last_bmc = time.monotonic()
                if self.check_bmc_status().get("power") == "on" and self._confirm_power_on():
                    return {"success": True, "error": None, "power": "on"}
            if time.monotonic() - last_ssh >= self.POWERON_SSH_PROBE_INTERVAL:
                last_ssh = time.monotonic()
                try:
                    probe = check_ssh_connectivity(refresh=True)
                except Exception as e:
                    probe = {"connected": False, "error": str(e)[:200]}
                if probe.get("connected"):
                    logger.info("Power-on verified via SSH for %s", config.host)
                    return {"success": True, "error": None, "power": "on",
                            "note": "verified via SSH"}
                if not probe.get("host_down") and \
                        not str(probe.get("error") or "").startswith("Remote host not configured"):
                    # TCP answered (even if sshd is not up yet): the chassis
                    # is on and the OS is booting — that is a successful power-on.
                    logger.info("Power-on verified via network for %s", config.host)
                    return {"success": True, "error": None, "power": "on",
                            "note": "verified via network"}
                last_ssh_error = probe.get("error") or "server not reachable"
            remain = deadline - time.monotonic()
            if remain <= 0:
                break
            time.sleep(min(1.0, remain))
        logger.warning(
            "Power-on not confirmed within %ss for %s (bmc: %s; ssh: %s)",
            self.POWERON_TOTAL_TIMEOUT, config.bmc_host, last_error, last_ssh_error)
        parts = []
        if last_error:
            parts.append(f"BMC: {last_error}")
        parts.append(f"server did not come online (SSH: {last_ssh_error or 'unreachable'})")
        return {
            "success": False,
            "error": "; ".join(parts),
        }

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
            "-E",
            "chassis", "power", "status",
        ]
        t_ipmi = time.monotonic()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=ipmi_timeout,
                                    env=_ipmitool_env(config.bmc_password))
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
