import asyncio
import codecs
import contextlib
import hashlib
import json
import logging
import threading
import time

from fastapi import WebSocket, WebSocketDisconnect

from ..config.remote_client import config_manager
from ..executor.remote.ssh import build_ssh_client
from ..middleware import ws_api_key_valid, ws_connections_total

logger = logging.getLogger(__name__)

WS_CHUNK_SIZE = 8192
CONSOLE_POLL_INTERVAL = 0.05
CONSOLE_MAX_INPUT_CHARS = 64 * 1024

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

class ConsoleSessionManager:

    _MAX_SESSIONS = 5
    _CREATING = object()

    def __init__(self):
        self._sessions: dict[str, ConsoleSession | object] = {}
        self._lock = threading.Lock()

    def _key(self, config):
        pw_hash = hashlib.sha256(
            (config.ssh_password or "").encode("utf-8")).hexdigest()[:12]
        return f"{config.host}:{config.ssh_port}:{config.ssh_username}:{pw_hash}"

    def get_or_create(self, config) -> tuple[ConsoleSession | None, bool]:
        key = self._key(config)
        with self._lock:
            stale_keys = [
                k for k, v in self._sessions.items()
                if v is not self._CREATING and not v.is_alive()
            ]
            for k in stale_keys:
                self._sessions[k].close()
                del self._sessions[k]
            session = self._sessions.get(key)
            if session is self._CREATING:
                return None, False
            if session is not None and session.is_alive():
                return session, False
            if len(self._sessions) >= self._MAX_SESSIONS:
                evicted = False
                for oldest_key, s in list(self._sessions.items()):
                    if s is self._CREATING:
                        continue
                    s.close()
                    del self._sessions[oldest_key]
                    evicted = True
                    break
                if not evicted:
                    return None, False
            self._sessions[key] = self._CREATING

        session = self._create(config)
        with self._lock:
            if self._sessions.get(key) is self._CREATING:
                if session is not None:
                    self._sessions[key] = session
                    return session, True
                del self._sessions[key]
            else:
                if session is not None:
                    session.close()
        return None, False

    def _create(self, config) -> ConsoleSession | None:
        try:
            ssh = build_ssh_client(
                host=config.host,
                port=config.ssh_port,
                username=config.ssh_username,
                password=config.ssh_password,
                key_path=config.ssh_key_path or "",
            )
        except Exception as e:
            logger.error(f"[Console] SSH connection failed: {e}")
            return None

        channel = ssh.invoke_shell(term="xterm-256color", width=200, height=50)
        channel.setblocking(0)

        session = ConsoleSession(ssh, channel)
        session.start_reader()
        logger.info(f"[Console] Created new session for {config.host}")
        return session

    def active_session_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values()
                       if s is not self._CREATING)

console_session_manager = ConsoleSessionManager()

async def websocket_console(websocket: WebSocket):
    if not ws_api_key_valid(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ws_connections_total.labels(ws="console").inc()

    config = config_manager.get_config()
    if not config.is_remote():
        await websocket.send_text(json.dumps({"type": "error", "data": "Console only available in remote mode"}))
        await websocket.close()
        return

    session, is_new = await asyncio.to_thread(console_session_manager.get_or_create, config)
    if not session:
        await websocket.send_text(json.dumps({"type": "error", "data": "Failed to connect to SSH"}))
        await websocket.close()
        return

    mode = websocket.query_params.get("mode", "reconnect")

    if not is_new and mode != "start":
        history, read_position = session.get_history_with_position()
        if history:
            try:
                for i in range(0, len(history), WS_CHUNK_SIZE):
                    chunk = history[i:i + WS_CHUNK_SIZE]
                    await websocket.send_text(json.dumps({"type": "output", "data": chunk}))
            except (WebSocketDisconnect, Exception):
                with contextlib.suppress(Exception):
                    await websocket.close()
                return
    else:
        read_position = session.total_written()

    await websocket.send_text(json.dumps({"type": "connected", "data": f"Connected to {config.host}"}))

    async def read_output():
        nonlocal read_position
        while True:
            try:
                new_data, read_position, needs_replace = session.get_output_since(read_position)
                if new_data:
                    if needs_replace:
                        await websocket.send_text(json.dumps({"type": "buffer_truncated", "data": ""}))
                    await websocket.send_text(json.dumps({"type": "output", "data": new_data}))
                if not session.is_alive():
                    break
                await asyncio.sleep(CONSOLE_POLL_INTERVAL)
            except Exception as e:
                logger.error(f"[Console] Read error: {e}")
                break

    read_task = asyncio.create_task(read_output())

    send_fail_announced = False

    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except TimeoutError:
                if not session.is_alive():
                    await websocket.send_text(json.dumps({"type": "error", "data": "Remote session closed"}))
                    break
                continue
            try:
                message = json.loads(msg)
            except (json.JSONDecodeError, TypeError):
                await websocket.send_text(json.dumps({
                    "type": "error", "data": "Invalid JSON format"
                }))
                continue
            if not isinstance(message, dict):
                await websocket.send_text(json.dumps({
                    "type": "error", "data": "Invalid message format"
                }))
                continue
            mtype = message.get("type")
            if mtype == "input":
                data = message.get("data", "")
                if isinstance(data, str) and data:
                    if len(data) > CONSOLE_MAX_INPUT_CHARS:
                        await websocket.send_text(json.dumps({
                            "type": "error", "data": "Input too large (max 64KB)"
                        }))
                        continue
                    if session.send_command(data):
                        send_fail_announced = False
                    elif not send_fail_announced:
                        send_fail_announced = True
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "data": "Input not delivered (PTY send window exhausted). Re-type your input."
                        }))
                continue
            if mtype == "resize":
                try:
                    cols = int(message.get("cols", 0))
                    rows = int(message.get("rows", 0))
                except (TypeError, ValueError):
                    continue
                if 2 <= cols <= 500 and 2 <= rows <= 200:
                    session.resize(cols, rows)
                continue
            cmd = message.get("command", "")
            if not isinstance(cmd, str) or not cmd:
                continue
            if len(cmd) > CONSOLE_MAX_INPUT_CHARS:
                await websocket.send_text(json.dumps({
                    "type": "error", "data": "Command too large (max 64KB)"
                }))
                continue
            if not cmd.endswith("\n"):
                cmd += "\n"
            if session.send_command(cmd):
                send_fail_announced = False
            elif not send_fail_announced:
                send_fail_announced = True
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "data": "Input not delivered (PTY send window exhausted). Re-type your input."
                }))
    except WebSocketDisconnect:
        pass
    finally:
        if not session.is_alive():
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="remote session closed")
        read_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await read_task
