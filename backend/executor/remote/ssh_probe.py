"""Shared SSH reachability probe for the dashboard.

Two layers feed the header's SSH light:

* **Network layer** — one fresh ICMP echo per request (see
  :mod:`.net_probe`), debounced by a 2-strike tracker: a single dropped
  packet is yellow (degraded), the light turns red on the second
  consecutive failed ping. The light never trusts a cached reachability
  value — the 2026-10-21 power-off incident kept the UI green because a
  wedged probe thread kept serving a stale "connected".
* **Service layer** — the background probe thread below (``echo ok`` over
  the shared SSH pool every second). Its cache provides the
  service-level fields (connected / latency); when the cache goes stale a
  bounded fresh round self-heals it, so a dead or wedged thread cannot pin
  a stale "connected" for minutes.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import threading
import time
from typing import Any

from .net_probe import icmp_ping, ssh_ping
from .ssh import ssh_pool

logger = logging.getLogger(__name__)

SSH_PROBE_CMD_TIMEOUT = 5

SSH_PROBE_INTERVAL = 1.0
# Audit 2026-10-22 BCK-2 residue: beyond this cache age the shared probe
# thread is treated as wedged and restarted — a wedged thread stays alive,
# so the `is_alive()` rebuild check alone never fires and the stale result
# (possibly 'connected' on a dead machine) is served forever.
SSH_PROBE_STALE_SECONDS = 60.0
# Self-heal: the probe thread refreshes its cache every second, so a cache
# older than this on a per-request read means the thread stopped making
# rounds (2026-10-21 power-off incident: a stale 'connected' result kept
# the UI green while the machine was off).
SSH_SERVICE_REFRESH_SECONDS = 5.0
# Wall-clock bound for a self-heal round (ping 1 s + echo 5 s ≈ 6 s worst
# case). The round runs in a one-shot executor; on timeout the last known
# state is served instead of blocking the request.
SSH_SERVICE_HEAL_TIMEOUT = 8.0

_ssh_probe_lock = threading.Lock()
_ssh_probe_cache: dict[str, Any] | None = None
_ssh_probe_fp: tuple | None = None
_ssh_probe_thread: threading.Thread | None = None
_ssh_probe_stop = threading.Event()

# Diagnostics for the 2026-10-21 power-off incident (the probe thread
# emitted no transition log across the real off window and the container
# was redeployed before the scene could be inspected): _ssh_probe_ts lets
# API readers detect a stale cache and the loop heartbeat proves thread
# liveness.
_ssh_probe_ts: float = 0.0
_stale_warn_ts: float = 0.0
# Last probe-thread restart (audit 2026-10-22 BCK-2): throttle the wedged
# rebuild so a still-wedged fresh thread is not rebuilt on every call.
_probe_restart_ts: float = 0.0

def _ssh_probe_fingerprint(config) -> tuple:
    return (
        config.host,
        config.ssh_port,
        config.ssh_username,
        config.ssh_password,
        config.ssh_key_path or "",
    )

def _classify_service_error(error: str) -> str:
    if "authentication" in error.lower() or "permission denied" in error.lower():
        return "auth"
    return "no_response"

def _probe_once(config) -> dict[str, Any]:
    if not config.host or config.host in ("localhost", "127.0.0.1"):
        return {
            "connected": False,
            "error": "Remote host not configured",
            "error_code": "not_configured",
            "host_down": True,
            "net_state": "down",
            "latency_ms": None,
        }

    start_time = time.time()
    # Network layer first: one ICMP echo (1 s budget). Ping failure means
    # the host is unreachable — skip the SSH round trip entirely (the fast
    # path behind the header light's red state).
    try:
        rtt = icmp_ping(config.host)
    except OSError:
        rtt = None
    if rtt is None:
        return {
            "connected": False,
            "host_down": True,
            "error": "host unreachable (ping no response)",
            "error_code": "ping",
            "net_state": "down",
            "latency_ms": round((time.time() - start_time) * 1000, 2),
        }
    client = None
    try:
        # Reuse the shared pool: when a healthy idle connection is pooled the
        # probe costs one channel round-trip, not a full SSH handshake
        # (this probe runs every second).
        client = ssh_pool.get(
            config.host,
            config.ssh_port,
            config.ssh_username,
            config.ssh_password,
            config.ssh_key_path or "",
        )
        _stdin, stdout, _stderr = client.exec_command(
            "echo ok", timeout=SSH_PROBE_CMD_TIMEOUT)
        out = stdout.read().decode("utf-8", errors="replace")
        latency_ms = round((time.time() - start_time) * 1000, 2)

        if out.strip() == "ok":
            return {
                "connected": True,
                "latency_ms": latency_ms,
                "host": config.host,
                "port": config.ssh_port,
                "username": config.ssh_username,
                "error_code": None,
                "net_state": "up",
            }
        else:
            return {
                "connected": False,
                "error": "SSH probe returned unexpected output",
                "error_code": "no_response",
                "net_state": "up",
                "latency_ms": latency_ms
            }
    except Exception as e:
        latency_ms = round((time.time() - start_time) * 1000, 2)
        return {
            "connected": False,
            "error": str(e)[:200],
            "error_code": _classify_service_error(str(e)),
            "net_state": "up",
            "latency_ms": latency_ms
        }
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                ssh_pool.release(
                    client, config.host, config.ssh_port, config.ssh_username)

def _run_ssh_probe() -> dict[str, Any]:
    global _ssh_probe_cache, _ssh_probe_fp, _ssh_probe_ts
    from ...config.remote_client import config_manager
    config = config_manager.get_config()
    result = _probe_once(config)
    with _ssh_probe_lock:
        _ssh_probe_cache = result
        _ssh_probe_fp = _ssh_probe_fingerprint(config)
        _ssh_probe_ts = time.monotonic()
    return result

def _ssh_probe_loop() -> None:
    last_connected: bool | None = None
    last_diag_ts = 0.0
    last_heartbeat = 0.0
    while not _ssh_probe_stop.is_set():
        time.sleep(SSH_PROBE_INTERVAL)
        if _ssh_probe_stop.is_set():
            return
        try:
            result = _run_ssh_probe()
        except Exception:
            logger.exception("SSH shared probe round failed (continues next round)")
            continue
        connected = bool(result.get("connected"))
        now = time.monotonic()
        if (last_connected is None
                or connected != last_connected
                or (not connected and now - last_diag_ts >= 30.0)):
            if connected:
                logger.info(
                    "SSH shared probe recovered: latency=%.0fms",
                    result.get("latency_ms") or 0)
            else:
                logger.warning(
                    "SSH shared probe failed: %s", result.get("error"))
            last_connected = connected
            last_diag_ts = now
        if now - last_heartbeat >= 900.0:
            logger.info(
                "SSH shared probe heartbeat: connected=%s latency_ms=%s",
                connected, result.get("latency_ms"))
            last_heartbeat = now
        if connected:
            time.sleep(SSH_PROBE_INTERVAL)
            if _ssh_probe_stop.is_set():
                return

def _self_heal_round() -> dict[str, Any] | None:
    """Run one bounded fresh probe round; ``None`` when it exceeds the cap.

    One-shot executor (same pattern as the BMC power-on path): an
    abandoned round keeps running in its thread and exits on the internal
    ping/echo timeouts, so a stuck round cannot pile up on a shared queue.
    """
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="ssh-probe-heal")
    try:
        return executor.submit(_run_ssh_probe).result(timeout=SSH_SERVICE_HEAL_TIMEOUT)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "SSH probe self-heal round exceeded %.0fs — serving last known state",
            SSH_SERVICE_HEAL_TIMEOUT)
        return None
    finally:
        executor.shutdown(wait=False)

def check_ssh_connectivity(refresh: bool = False) -> dict[str, Any]:
    global _ssh_probe_thread, _stale_warn_ts, _probe_restart_ts
    from ...config.remote_client import config_manager
    config = config_manager.get_config()

    if refresh:
        # Model start / power-on path: force a full fresh round (ping +
        # echo); the caller may block on the probe itself.
        return _run_ssh_probe()

    if not config.host or config.host in ("localhost", "127.0.0.1"):
        return {
            "connected": False,
            "error": "Remote host not configured",
            "error_code": "not_configured",
            "host_down": True,
            "net_state": "down",
            "latency_ms": None,
        }

    # Network layer: a fresh per-request ping (1 s) feeds the 2-strike
    # tracker. The light never reads a cached reachability value.
    try:
        rtt = icmp_ping(config.host)
    except OSError:
        rtt = None
    net_state, ping_rtt = ssh_ping.record(config.host, rtt)

    service: dict[str, Any] | None = None
    age = 0.0
    with _ssh_probe_lock:
        if _ssh_probe_cache is not None and _ssh_probe_fp == _ssh_probe_fingerprint(config):
            age = time.monotonic() - _ssh_probe_ts
            if age > SSH_PROBE_STALE_SECONDS:
                # A wedged probe thread stays alive, so the is_alive()
                # check below never rebuilds it and the stale result
                # (possibly 'connected' on a dead machine) would be served
                # forever. Beyond the stale bound, restart the thread and
                # keep serving the old value until the first new round.
                # Throttled so a still-wedged fresh thread is not rebuilt
                # on every caller (audit 2026-10-22 BCK-2).
                if time.monotonic() - _probe_restart_ts >= SSH_PROBE_STALE_SECONDS:
                    logger.warning(
                        "SSH shared probe cache %.0fs stale — restarting the "
                        "probe thread (audit 2026-10-22 BCK-2)", age)
                    if _ssh_probe_thread is not None:
                        _ssh_probe_stop.set()
                        _ssh_probe_thread.join(timeout=2.0)
                    _ssh_probe_stop.clear()
                    _ssh_probe_thread = threading.Thread(
                        target=_ssh_probe_loop, name="ssh-probe", daemon=True)
                    _ssh_probe_thread.start()
                    _probe_restart_ts = time.monotonic()
                elif time.monotonic() - _stale_warn_ts >= 30.0:
                    _stale_warn_ts = time.monotonic()
                    logger.warning(
                        "SSH shared probe cache %.0fs stale after restart "
                        "(thread still wedged)", age)
            service = dict(_ssh_probe_cache)
        elif _ssh_probe_thread is None or not _ssh_probe_thread.is_alive():
            _ssh_probe_stop.clear()
            _ssh_probe_thread = threading.Thread(
                target=_ssh_probe_loop, name="ssh-probe", daemon=True)
            _ssh_probe_thread.start()

    if service is not None and age > SSH_SERVICE_REFRESH_SECONDS:
        healed = _self_heal_round()
        if healed is not None:
            service = healed

    if service is None:
        result: dict[str, Any] = {
            "connected": False,
            "error": "SSH probe not ready",
            "error_code": "no_response",
            "latency_ms": None,
        }
    else:
        result = dict(service)

    # Host unreachable per the tracker: the service layer cannot be true.
    if net_state == "down" and result.get("connected"):
        result["connected"] = False
        result["error"] = "host unreachable (ping no response)"
        result["error_code"] = "ping"
    result["net_state"] = net_state
    result["host_down"] = net_state != "up"
    if ping_rtt is not None:
        result["ping_rtt_ms"] = round(ping_rtt * 1000, 2)
    return result
