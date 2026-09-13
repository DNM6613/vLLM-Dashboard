import contextlib
import os
import socket
import threading
import time
from collections import OrderedDict
from typing import Any

from ...config.settings import settings

TCP_KEEPALIVE_IDLE = 30
TCP_KEEPALIVE_INTERVAL = 10
TCP_KEEPALIVE_PROBES = 3

def configure_tcp_keepalive(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    with contextlib.suppress(OSError, AttributeError):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, TCP_KEEPALIVE_IDLE)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, TCP_KEEPALIVE_INTERVAL)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, TCP_KEEPALIVE_PROBES)

class ChannelOpenFailure(Exception):
    pass

def _known_hosts_path() -> str:
    current = os.path.abspath(__file__)
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(current))))
    return os.path.join(project_root, "data", "ssh_known_hosts")

_KNOWN_HOSTS_LOCK = threading.Lock()

def _ssh_handshake(client: Any, host: str, port: int, username: str,
                   password: str, key_path: str, sock: socket.socket) -> None:
    if key_path:
        client.connect(host, port=port, username=username, key_filename=key_path, sock=sock)
    elif password:
        client.connect(host, port=port, username=username, password=password, sock=sock)
    else:
        client.connect(host, port=port, username=username, sock=sock)

def build_ssh_client(
    host: str,
    port: int,
    username: str,
    password: str = "",
    key_path: str = "",
) -> Any:
    import paramiko

    connect_timeout = settings.SSH_CONNECT_TIMEOUT

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    sock = socket.create_connection((host, port), timeout=connect_timeout)
    sock.settimeout(None)
    configure_tcp_keepalive(sock)

    if settings.SSH_STRICT_HOST_KEY:
        known_hosts = _known_hosts_path()
        os.makedirs(os.path.dirname(known_hosts), exist_ok=True)
        if not os.path.exists(known_hosts):
            with open(known_hosts, "a"):
                pass
        with _KNOWN_HOSTS_LOCK:
            client.load_host_keys(known_hosts)
            _ssh_handshake(client, host, port, username, password, key_path, sock)
    else:
        _ssh_handshake(client, host, port, username, password, key_path, sock)

    try:
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(15)
    except Exception:
        pass

    return client

class SSHConnectionPool:

    def __init__(self, max_idle_time: float = 30.0, max_connections: int = 10,
                 lock_timeout: float = 30.0):
        self._pool: OrderedDict[tuple[str, int, str], tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._conn_locks: dict[tuple[str, int, str], threading.Lock] = {}
        self._max_idle_time = max_idle_time
        self._max_connections = max_connections
        self._lock_timeout = lock_timeout

    def _get_conn_lock(self, key: tuple[str, int, str]) -> threading.Lock:
        with self._lock:
            if key not in self._conn_locks:
                self._conn_locks[key] = threading.Lock()
            return self._conn_locks[key]

    def get(self, host: str, port: int, username: str, password: str = "", key_path: str = "") -> Any:
        key = (host, port, username)
        conn_lock = self._get_conn_lock(key)
        if not conn_lock.acquire(timeout=self._lock_timeout):
            raise TimeoutError(
                f"Timed out after {self._lock_timeout:.0f}s waiting for SSH "
                f"connection lock ({host}:{port} {username})"
            )

        try:
            with self._lock:
                if key in self._pool:
                    client, last_used = self._pool[key]
                    try:
                        transport = client.get_transport()
                        if transport and not transport.is_active():
                            client.close()
                            del self._pool[key]
                        elif time.time() - last_used > self._max_idle_time:
                            client.close()
                            del self._pool[key]
                        else:
                            self._pool.move_to_end(key)
                            self._pool[key] = (client, time.time())
                            return client
                    except Exception:
                        with contextlib.suppress(Exception):
                            client.close()
                        del self._pool[key]

            client = self._create_client(host, port, username, password, key_path)
            with self._lock:
                if key in self._pool:
                    existing_client, last_used = self._pool[key]
                    try:
                        transport = existing_client.get_transport()
                        if (transport and transport.is_active()
                                and (time.time() - last_used) <= self._max_idle_time):
                            with contextlib.suppress(Exception):
                                client.close()
                            self._pool[key] = (existing_client, time.time())
                            return existing_client
                        else:
                            with contextlib.suppress(Exception):
                                existing_client.close()
                    except Exception:
                        pass

                if len(self._pool) >= self._max_connections:
                    for candidate in list(self._pool):
                        lock = self._conn_locks.get(candidate)
                        if lock is not None and lock.locked():
                            continue
                        oldest_client, _ = self._pool.pop(candidate, None)
                        with contextlib.suppress(Exception):
                            oldest_client.close()
                        break

                self._pool[key] = (client, time.time())
            return client
        except Exception:
            conn_lock.release()
            raise

    def _create_client(self, host: str, port: int, username: str, password: str = "", key_path: str = "") -> Any:
        return build_ssh_client(host, port, username, password, key_path)

    def release(self, client: Any, host: str, port: int, username: str) -> None:
        key = (host, port, username)
        try:
            with self._lock:
                try:
                    transport = client.get_transport()
                    if not transport or not transport.is_active():
                        with contextlib.suppress(Exception):
                            client.close()
                        self._pool.pop(key, None)
                        return
                except Exception:
                    with contextlib.suppress(Exception):
                        client.close()
                    self._pool.pop(key, None)
                    return

                if key in self._pool:
                    self._pool[key] = (client, time.time())
                else:
                    client.close()
        finally:
            self._get_conn_lock(key).release()

    def evict(self, client: Any, host: str, port: int, username: str) -> None:
        key = (host, port, username)
        with self._lock:
            pooled = self._pool.pop(key, None)
        if pooled is not None and pooled[0] is not client:
            with contextlib.suppress(Exception):
                pooled[0].close()
        with contextlib.suppress(Exception):
            client.close()
        conn_lock = self._get_conn_lock(key)
        if conn_lock.locked():
            conn_lock.release()

ssh_pool = SSHConnectionPool(lock_timeout=settings.SSH_CMD_TIMEOUT * 2)
