import concurrent.futures
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any

from .net_probe import bmc_ping, icmp_ping

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

def _bmc_diag(host: str, net_state: str, ipmi: str, ipmi_ms: float,
              connected: bool, power: Any) -> None:
    """Throttled (15 s) single-line BMC probe diagnostic.

    ``ipmi`` is the ipmitool outcome (``ok`` / ``timeout`` / ``fail(rc)`` /
    ``skip`` — skipped on the ping fast path when the host does not answer
    ICMP).
    """
    global _bmc_diag_last, _bmc_diag_last_ts
    key = (connected, ipmi == "timeout")
    now = time.monotonic()
    with _bmc_diag_lock:
        if (_bmc_diag_last is not None and _bmc_diag_last == key
                and now - _bmc_diag_last_ts < 15.0):
            return
        _bmc_diag_last = key
        _bmc_diag_last_ts = now
    logger.info(
        "BMC probe diag: host=%s ping=%s ipmi=%s(%.0fms) connected=%s power=%s",
        host, net_state, ipmi, ipmi_ms, connected, power)

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
            "-P", str(config.bmc_rmcp_port),
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
                    # Audit 2026-10-22 BCK-2: refresh=True is a full SSH
                    # round; this is a blocking power-on verify loop, so an
                    # unbounded round would stall the whole BMC retry loop.
                    # Run it on a worker thread with a wall-clock bound (no
                    # ``with`` — the context manager would wait=True for the
                    # still-running task after the timeout, negating it).
                    _pexec = concurrent.futures.ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix="ssh-probe-refresh")
                    try:
                        probe = _pexec.submit(
                            check_ssh_connectivity, True).result(timeout=60.0)
                    finally:
                        _pexec.shutdown(wait=False)
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

    def check_bmc_status(self) -> dict[str, Any]:
        """Check BMC (IPMI) connectivity + chassis power state.

        Network layer = one fresh ICMP ping per request fed to the
        2-strike tracker (:class:`~.net_probe.PingTracker`): on ping
        failure the ipmitool round trip is skipped entirely (fast path —
        no subprocess on a dead host). Success =
        ``ipmitool chassis power status`` answered within
        ``BMC_STATUS_TIMEOUT``; a failed session with bad credentials is
        classified ``auth``, anything else ``no_response``.
        """
        from ...config.remote_client import config_manager
        config = config_manager.get_config()

        if not config.bmc_host:
            return {"configured": False, "connected": False, "power": None,
                    "error": None, "error_code": "not_configured",
                    "net_state": "up", "ping_rtt_ms": None}
        if not config.bmc_password:
            return {"configured": True, "connected": False, "power": None,
                    "error": "BMC password not configured (Server Config: BMC Password)",
                    "error_code": "not_configured",
                    "net_state": "up", "ping_rtt_ms": None}

        ipmitool_path = shutil.which("ipmitool")
        if not ipmitool_path:
            return {"configured": True, "connected": False, "power": None,
                    "error": "ipmitool not available on dashboard host",
                    "error_code": "no_response",
                    "net_state": "up", "ping_rtt_ms": None}

        try:
            rtt = icmp_ping(config.bmc_host)
        except OSError:
            rtt = None
        net_state, ping_rtt = bmc_ping.record(config.bmc_host, rtt)
        ping_rtt_ms = round(ping_rtt * 1000, 2) if ping_rtt is not None else None
        if net_state != "up":
            # Fast path: the BMC does not answer ICMP (1–3 strikes) — do not
            # run ipmitool. Power is unknown this round; the frontend holds
            # the last known reading.
            _bmc_diag(config.bmc_host, net_state, "skip", 0.0, False, None)
            return {"configured": True, "connected": False, "power": None,
                    "error": "BMC unreachable (ping no response)",
                    "error_code": "ping",
                    "net_state": net_state, "ping_rtt_ms": ping_rtt_ms}

        cmd = [
            ipmitool_path, "-I", "lanplus",
            "-H", config.bmc_host,
            "-U", config.bmc_username or "admin",
            "-P", str(config.bmc_rmcp_port),
            "-E",
            "chassis", "power", "status",
        ]
        t_ipmi = time.monotonic()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=self.BMC_STATUS_TIMEOUT,
                                    env=_ipmitool_env(config.bmc_password))
        except subprocess.TimeoutExpired:
            ipmi_ms = (time.monotonic() - t_ipmi) * 1000
            _bmc_diag(config.bmc_host, net_state, "timeout", ipmi_ms, False, None)
            return {"configured": True, "connected": False, "power": None,
                    "error": "BMC status probe timeout (no response from BMC)",
                    "error_code": "no_response",
                    "net_state": net_state, "ping_rtt_ms": ping_rtt_ms}
        except Exception as e:
            ipmi_ms = (time.monotonic() - t_ipmi) * 1000
            _bmc_diag(config.bmc_host, net_state, "fail", ipmi_ms, False, None)
            return {"configured": True, "connected": False, "power": None,
                    "error": f"BMC status probe failed: {e}",
                    "error_code": "no_response",
                    "net_state": net_state, "ping_rtt_ms": ping_rtt_ms}
        ipmi_ms = (time.monotonic() - t_ipmi) * 1000

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            code = "auth" if "unable to establish ipmi" in err.lower() else "no_response"
            detail = ("Cannot reach BMC or BMC authentication failed "
                      "(check BMC address and credentials)"
                      if code == "auth"
                      else (err[:200] or f"ipmitool exited with code {result.returncode}"))
            logger.debug("BMC status probe failed for %s (rc=%s)", config.bmc_host, result.returncode)
            _bmc_diag(config.bmc_host, net_state, f"fail({result.returncode})", ipmi_ms, False, None)
            return {"configured": True, "connected": False, "power": None,
                    "error": detail, "error_code": code,
                    "net_state": net_state, "ping_rtt_ms": ping_rtt_ms}

        out = (result.stdout or "").lower()
        power = "on" if "power is on" in out else ("off" if "power is off" in out else None)
        _bmc_diag(config.bmc_host, net_state, "ok", ipmi_ms, True, power)
        return {"configured": True, "connected": True, "power": power,
                "error": None, "error_code": None,
                "net_state": net_state, "ping_rtt_ms": ping_rtt_ms}
