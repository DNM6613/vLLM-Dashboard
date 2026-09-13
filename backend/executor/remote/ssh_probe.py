import contextlib
import logging
import socket
import threading
import time
from typing import Any

from .ssh import build_ssh_client

logger = logging.getLogger(__name__)

SSH_PROBE_CMD_TIMEOUT = 5
SSH_REACH_PROBE_TIMEOUT = 1

SSH_PROBE_INTERVAL = 1.0

_ssh_probe_lock = threading.Lock()
_ssh_probe_cache: dict[str, Any] | None = None
_ssh_probe_fp: tuple | None = None
_ssh_probe_thread: threading.Thread | None = None
_ssh_probe_stop = threading.Event()

def _ssh_probe_fingerprint(config) -> tuple:
    return (
        config.host,
        config.ssh_port,
        config.ssh_username,
        config.ssh_password,
        config.ssh_key_path or "",
    )

def _probe_once(config) -> dict[str, Any]:
    if not config.host or config.host in ("localhost", "127.0.0.1"):
        return {
            "connected": False,
            "error": "Remote host not configured",
            "latency_ms": None
        }

    start_time = time.time()
    try:
        with socket.create_connection(
                (config.host, config.ssh_port),
                timeout=SSH_REACH_PROBE_TIMEOUT):
            pass
    except ConnectionRefusedError:
        pass
    except OSError:
        return {
            "connected": False,
            "host_down": True,
            "error": "host unreachable (TCP probe no response)",
            "latency_ms": round((time.time() - start_time) * 1000, 2),
        }
    client = None
    try:
        client = build_ssh_client(
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
                "username": config.ssh_username
            }
        else:
            return {
                "connected": False,
                "error": "SSH probe returned unexpected output",
                "latency_ms": latency_ms
            }
    except Exception as e:
        latency_ms = round((time.time() - start_time) * 1000, 2)
        return {
            "connected": False,
            "error": str(e)[:200],
            "latency_ms": latency_ms
        }
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()

def _run_ssh_probe() -> dict[str, Any]:
    global _ssh_probe_cache, _ssh_probe_fp
    from ...config.remote_client import config_manager
    config = config_manager.get_config()
    result = _probe_once(config)
    with _ssh_probe_lock:
        _ssh_probe_cache = result
        _ssh_probe_fp = _ssh_probe_fingerprint(config)
    return result

def _ssh_probe_loop() -> None:
    last_connected: bool | None = None
    last_diag_ts = 0.0
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
        if connected:
            time.sleep(SSH_PROBE_INTERVAL)
            if _ssh_probe_stop.is_set():
                return

def check_ssh_connectivity(refresh: bool = False) -> dict[str, Any]:
    global _ssh_probe_thread
    from ...config.remote_client import config_manager
    config = config_manager.get_config()

    if refresh:
        return _run_ssh_probe()

    if not config.host or config.host in ("localhost", "127.0.0.1"):
        return {
            "connected": False,
            "error": "Remote host not configured",
            "latency_ms": None
        }

    with _ssh_probe_lock:
        if (_ssh_probe_cache is not None
                and _ssh_probe_fp == _ssh_probe_fingerprint(config)):
            return dict(_ssh_probe_cache)
        if _ssh_probe_thread is None or not _ssh_probe_thread.is_alive():
            _ssh_probe_stop.clear()
            _ssh_probe_thread = threading.Thread(
                target=_ssh_probe_loop, name="ssh-probe", daemon=True)
            _ssh_probe_thread.start()

    return {
        "connected": False,
        "error": "SSH probe not ready",
        "latency_ms": None,
    }
