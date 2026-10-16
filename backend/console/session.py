"""ConsoleSession: buffered PTY reader/writer for one SSH console session.

Split out of ``session_manager.py`` (file-size gate). The session manager
and the WebSocket endpoint live in ``console/session_manager.py``.
"""

from __future__ import annotations

import codecs
import contextlib
import logging
import threading
import time

logger = logging.getLogger(__name__)


class ConsoleSession:

    _MAX_BUFFER_SIZE = 100000
    _KEEP_TAIL_SIZE = _MAX_BUFFER_SIZE // 2
    _SEND_DROP_WARN_COOLDOWN = 5.0

    def __init__(self, ssh, channel):
        self.ssh = ssh
        self.channel = channel
        self._buffer = ""
        self._lock = threading.Lock()
        self._running = True
        self._reader_thread = None
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._base_offset = 0
        self._total_written = 0
        self._last_send_drop_warn = 0.0
        # Number of WebSockets currently attached. Mutated under the
        # manager's lock (see ConsoleSessionManager.attach/detach).
        self.active_ws = 0

    def _append_locked(self, data: str) -> None:
        self._buffer += data
        self._total_written += len(data)
        if len(self._buffer) > self._MAX_BUFFER_SIZE:
            self._buffer = self._buffer[-self._KEEP_TAIL_SIZE:]
            self._base_offset = self._total_written - len(self._buffer)

    def start_reader(self):
        def _read():
            try:
                while self._running:
                    try:
                        if self.channel.closed:
                            self._running = False
                            break
                        if self.channel.recv_ready():
                            raw = self.channel.recv(4096)
                            data = self._decoder.decode(raw, False)
                            if data:
                                with self._lock:
                                    self._append_locked(data)
                        elif self.channel.exit_status_ready():
                            self._running = False
                            break
                        else:
                            time.sleep(0.05)
                    except Exception:
                        self._running = False
                        break
            finally:
                tail = ""
                with contextlib.suppress(Exception):
                    tail = self._decoder.decode(b"", True)
                if tail:
                    with self._lock:
                        self._append_locked(tail)
        self._reader_thread = threading.Thread(target=_read, daemon=True)
        self._reader_thread.start()

    def get_output_since(self, position: int) -> tuple[str, int, bool]:
        with self._lock:
            if position >= self._total_written:
                return '', self._total_written, False
            if position < self._base_offset:
                return self._buffer, self._total_written, True
            return self._buffer[position - self._base_offset:], self._total_written, False

    def get_all_output(self) -> str:
        with self._lock:
            return self._buffer

    def get_history_with_position(self) -> tuple[str, int]:
        with self._lock:
            return self._buffer, self._total_written

    def total_written(self) -> int:
        with self._lock:
            return self._total_written

    def _warn_send_drop(self, n_chars: int, reason: str) -> None:
        now = time.monotonic()
        if now - self._last_send_drop_warn < self._SEND_DROP_WARN_COOLDOWN:
            return
        self._last_send_drop_warn = now
        logger.warning(f"[Console] Input not delivered — {n_chars} char(s) dropped ({reason})")

    def send_command(self, cmd: str) -> bool:
        try:
            self.channel.sendall(cmd.encode('utf-8'))
            return True
        except (TimeoutError, BlockingIOError):
            self._warn_send_drop(len(cmd), "PTY send window exhausted, sendall timeout")
            return False
        except Exception as e:
            self._warn_send_drop(len(cmd), f"channel write failed: {e}")
            return False

    def resize(self, cols: int, rows: int) -> None:
        with contextlib.suppress(Exception):
            self.channel.resize_pty(width=cols, height=rows)

    def is_alive(self) -> bool:
        return self._running

    def close(self):
        self._running = False
        with contextlib.suppress(BaseException):
            self.channel.close()
        with contextlib.suppress(BaseException):
            self.ssh.close()
