import contextlib
import logging
import re
import shlex
import subprocess
from typing import Any

from ..config.server_config import has_parent_path_segment
from ..config.settings import settings
from .remote.cli_ops import CliOps
from .remote.model_ops import ModelOps
from .remote.monitor_ops import MonitorOps
from .remote.power_ops import PowerOps
from .remote.process_ops import ProcessOps
from .remote.ssh import ChannelOpenFailure, ssh_pool

logger = logging.getLogger(__name__)

_SHELL_META_PATTERN = re.compile(r'[|;&<>`]|\$\(|\$\{')

def get_remote_executor() -> "RemoteExecutor":
    from ..config.remote_client import config_manager
    config = config_manager.get_config()
    return RemoteExecutor(
        host=config.host,
        username=config.ssh_username,
        password=config.ssh_password,
        key_path=config.ssh_key_path or "",
        port=config.ssh_port
    )

class RemoteExecutor(MonitorOps, ProcessOps, PowerOps, CliOps, ModelOps):

    def __init__(self, host: str = "", username: str = "", password: str = "", key_path: str = "", port: int = 22):
        self.host = host
        self.username = username
        self.password = password
        self.key_path = key_path
        self.port = port

    def _is_remote(self) -> bool:
        return self.host != "" and self.host not in ("localhost", "127.0.0.1")

    def _exec_local(self, command: str, timeout: int = None) -> dict[str, Any]:
        if _SHELL_META_PATTERN.search(command):
            return {
                "success": False,
                "stdout": "",
                "stderr": (
                    "Localhost mode (shell=False) does not support shell operators "
                    "(|, &, ;, $, >, <, `). Use remote mode for composite commands."
                ),
                "returncode": -1
            }
        try:
            result = subprocess.run(
                shlex.split(command),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout if timeout else 60
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": "Command timeout",
                "returncode": -1
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1
            }

    def _exec_remote(self, command: str, timeout: int = None) -> dict[str, Any]:
        actual_timeout = timeout if timeout else settings.SSH_CMD_TIMEOUT
        try:
            stdout_text, stderr_text, returncode = self._open_and_exec(command, actual_timeout)
            return {
                "success": returncode == 0,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "returncode": returncode
            }
        except ChannelOpenFailure as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"SSH channel open failed: {e}",
                "returncode": -1,
                "channel_open_failed": True,
            }
        except TimeoutError:
            return {
                "success": False,
                "stdout": "",
                "stderr": "SSH command execution timeout",
                "returncode": -1
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1
            }

    def _open_and_exec(self, command: str, actual_timeout: int) -> tuple[str, str, int]:
        ssh = ssh_pool.get(self.host, self.port, self.username, self.password, self.key_path)
        evicted = False
        fresh = None
        open_done = False
        try:
            try:
                _stdin, stdout, stderr = ssh.exec_command(command, timeout=actual_timeout)
                open_done = True
            except Exception as e:
                logger.warning(
                    "SSH channel open failed on pooled connection to %s:%s (%s); "
                    "evicting and retrying once", self.host, self.port, e)
                ssh_pool.evict(ssh, self.host, self.port, self.username)
                evicted = True
                fresh = ssh_pool.get(self.host, self.port, self.username, self.password, self.key_path)
                _stdin, stdout, stderr = fresh.exec_command(command, timeout=actual_timeout)
                open_done = True
            try:
                stdout_text = stdout.read().decode('utf-8')
                stderr_text = stderr.read().decode('utf-8')
                returncode = stdout.channel.recv_exit_status()
            finally:
                with contextlib.suppress(Exception):
                    stdout.channel.close()
            return stdout_text, stderr_text, returncode
        except ChannelOpenFailure:
            raise
        except Exception as e:
            if evicted and not open_done:
                raise ChannelOpenFailure(str(e)) from e
            raise
        finally:
            if evicted:
                if fresh is not None:
                    ssh_pool.release(fresh, self.host, self.port, self.username)
            else:
                ssh_pool.release(ssh, self.host, self.port, self.username)

    def execute(self, command: str, timeout: int = None) -> dict[str, Any]:
        if self._is_remote():
            return self._exec_remote(command, timeout)
        return self._exec_local(command, timeout)

    _DQ_ESCAPE_RE = re.compile(r'([\\$`"])')

    def _escape_double_quoted(self, s: str) -> str:
        return self._DQ_ESCAPE_RE.sub(r'\\\1', s)

    def _get_activate_cmd(self) -> str:
        from ..config.remote_client import config_manager
        venv_name = config_manager.get_config().venv_name
        if not venv_name:
            return ""

        if has_parent_path_segment(venv_name) or not re.fullmatch(r'[\w/\-.~]+', venv_name):
            logger.warning(f"Invalid venv_name rejected: {venv_name}")
            return ""

        if venv_name.startswith("/"):
            venv_path = venv_name
        elif venv_name.startswith("~/"):
            venv_path = venv_name
        else:
            venv_path = f"$HOME/{venv_name}"

        return f"source {venv_path}/bin/activate && "
