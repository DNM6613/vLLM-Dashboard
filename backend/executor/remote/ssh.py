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
    # Target is a user-configured trusted internal host; strict host-key
    # verification is opt-in via settings.SSH_STRICT_HOST_KEY.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507

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
    """Reusable paramiko clients per (host, port, username).

    Concurrency model: the per-key conn_lock only serializes the
    acquire/create phase — it is released as soon as a client is handed
    out, so commands run concurrently on the same transport (paramiko
    multiplexes concurrent channels per connection). Previously the lock
    was held for the whole command, so one long remote command (e.g. the
    140s model scan) made every other command to the same host wait and
    then fail with a lock timeout.
    """

    def __init__(self, max_idle_time: float = 30.0, max_connections: int = 10,
                 lock_timeout: float = 30.0):
        self._pool: OrderedDict[tuple[str, int, str], tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._conn_locks: dict[tuple[str, int, str], threading.Lock] = {}
        # Holders currently using each pooled connection (in-flight commands).
        self._refcounts: dict[tuple[str, int, str], int] = {}
        self._max_idle_time = max_idle_time
        self._max_connections = max_connections
        self._lock_timeout = lock_timeout

    def _get_conn_lock(self, key: tuple[str, int, str]) -> threading.Lock:
        with self._lock:
            if key not in self._conn_locks:
                self._conn_locks[key] = threading.Lock()
            return self._conn_locks[key]

    def _is_alive(self, client: Any) -> bool:
        try:
            transport = client.get_transport()
            return bool(transport and transport.is_active())
        except Exception:
            return False

    def get(self, host: str, port: int, username: str, password: str = "", key_path: str = "") -> Any:
        key = (host, port, username)
        conn_lock = self._get_conn_lock(key)
        if not conn_lock.acquire(timeout=self._lock_timeout):
            raise TimeoutError(
                f"Timed out after {self._lock_timeout:.0f}s waiting for SSH "
                f"connection lock ({host}:{port} {username})"
            )
        try:
            client = self._get_or_create_locked(
                key, host, port, username, password, key_path)
        except Exception:
            with self._lock:
                self._refcounts[key] = max(0, self._refcounts.get(key, 0) - 1)
                if self._refcounts.get(key, 0) == 0:
                    self._refcounts.pop(key, None)
            conn_lock.release()
            raise
        # The lock only covered acquire/create: the caller now runs its
        # command without holding conn_lock.
        conn_lock.release()
        return client

    def _get_or_create_locked(
        self, key: tuple[str, int, str], host: str, port: int, username: str,
        password: str, key_path: str,
    ) -> Any:
        """Called with the per-key conn_lock held. Returns a usable client
        and registers one holder reference for it."""
        with self._lock:
            self._refcounts[key] = self._refcounts.get(key, 0) + 1
            if key in self._pool:
                client, last_used = self._pool[key]
                if self._is_alive(client) and time.time() - last_used <= self._max_idle_time:
                    self._pool.move_to_end(key)
                    self._pool[key] = (client, time.time())
                    return client
                with contextlib.suppress(Exception):
                    client.close()
                del self._pool[key]

        client = self._create_client(host, port, username, password, key_path)
        with self._lock:
            if key in self._pool:
                existing_client, last_used = self._pool[key]
                if (self._is_alive(existing_client)
                        and time.time() - last_used <= self._max_idle_time):
                    # Another thread created a healthy connection while we
                    # were in _create_client — reuse it, discard ours.
                    with contextlib.suppress(Exception):
                        client.close()
                    self._pool[key] = (existing_client, time.time())
                    return existing_client
                with contextlib.suppress(Exception):
                    existing_client.close()

            if len(self._pool) >= self._max_connections:
                self._evict_oldest_idle()

            self._pool[key] = (client, time.time())
            return client

    def _create_client(self, host: str, port: int, username: str, password: str = "", key_path: str = "") -> Any:
        return build_ssh_client(host, port, username, password, key_path)

    def _evict_oldest_idle(self) -> None:
        """Drop the oldest pooled connection that no command is using.
        Must be called with self._lock held."""
        for candidate in list(self._pool):
            if self._refcounts.get(candidate, 0) > 0:
                continue
            oldest_client, _ = self._pool.pop(candidate, None)
            with contextlib.suppress(Exception):
                oldest_client.close()
            break

    def release(self, client: Any, host: str, port: int, username: str) -> None:
        """Return a client after its command finished. A dead connection is
        dropped from the pool — but only when the pool entry still points at
        this exact client, so a replacement pooled by another thread is
        never dropped (which would leak it)."""
        key = (host, port, username)
        with self._lock:
            self._refcounts[key] = max(0, self._refcounts.get(key, 0) - 1)
            if self._refcounts[key] == 0:
                self._refcounts.pop(key, None)
            if not self._is_alive(client):
                with contextlib.suppress(Exception):
                    client.close()
                pooled = self._pool.get(key)
                if pooled is not None and pooled[0] is client:
                    self._pool.pop(key, None)
                return
            pooled = self._pool.get(key)
            if pooled is not None and pooled[0] is client:
                self._pool[key] = (client, time.time())
                self._pool.move_to_end(key)
            else:
                # This client is no longer the pooled one (a newer connection
                # superseded it) — close it so it does not linger.
                with contextlib.suppress(Exception):
                    client.close()

    def evict(self, client: Any, host: str, port: int, username: str) -> None:
        """Drop ``client`` after a channel-open failure and close it. If the
        pool entry at this key is already a different (newer) connection,
        that entry is left untouched — it belongs to another in-flight
        command."""
        key = (host, port, username)
        with self._lock:
            self._refcounts[key] = max(0, self._refcounts.get(key, 0) - 1)
            if self._refcounts[key] == 0:
                self._refcounts.pop(key, None)
            pooled = self._pool.get(key)
            if pooled is not None and pooled[0] is client:
                self._pool.pop(key, None)
        with contextlib.suppress(Exception):
            client.close()

ssh_pool = SSHConnectionPool(lock_timeout=settings.SSH_CMD_TIMEOUT * 2)
