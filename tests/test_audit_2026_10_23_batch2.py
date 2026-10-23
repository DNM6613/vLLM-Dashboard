"""Audit 2026-10-23 batch 2 fixes: E-2 (build_tar warns on untracked new
files that would silently miss the image), C-2 (the deploy API key travels
SFTP into a 0600 file — never a remote command line or the container env),
A-1 (the zombie downgrade honors is_stopping), A-2 (a manual stop cancels
the scheduled watchdog restart), C-1 (oversized request bodies are
rejected with 413 before FastAPI buffers them), C-3 (the HF token no
longer sits in the remote command line)."""
import asyncio
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.config import remote_client
from backend.controller.state_machine import state_machine
from backend.executor.remote.model_ops import ModelOps
from backend.executor.remote_executor import RemoteExecutor
from backend.middleware import body_size_gate_middleware
from backend.schemas.model import ModelStatus
from backend.service import (
    model_service,
    model_watchdog,
)
from tools import server_deploy as sd

# model_lifecycle / model_status_sync must be imported only after the
# facade is fully loaded: they bind the facade on import, and importing
# one first re-enters the still-initializing facade (circular from-import).
from backend.service import model_lifecycle, model_status_sync  # isort: skip


class _ZombieResp:
    """Fake HTTP response for the zombie-downgrade sync tests. Module
    level (not nested in the test case) because the fake client below is
    a nested class whose ``self`` cannot see the test case's attributes."""

    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._data


# ---------------------------------------------------------------------------
# E-2: build_tar warning for untracked new files
# ---------------------------------------------------------------------------

class TestBuildTarUntrackedWarning(unittest.TestCase):
    """audit 2026-10-23 E-2: build_tar ships ONLY tracked files, so a new
    source file that was never ``git add``ed is silently missing from the
    image (incident 2026-10-23: the missing vllm_metrics.py crashed the
    container at startup) — it must be surfaced loudly."""

    def _build(self, tmp: str, tracked: list[str], untracked: list[str]) -> str:
        for name in tracked:
            path = os.path.join(tmp, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("x")

        def fake_run(cmd, **kwargs):
            m = mock.Mock()
            m.returncode = 0
            if "--others" in cmd:
                m.stdout = ("\0".join(untracked) + "\0").encode() if untracked else b""
            else:
                m.stdout = ("\0".join(tracked) + "\0").encode()
            return m

        buf = io.StringIO()
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            with mock.patch.object(sd.subprocess, "run", side_effect=fake_run), \
                    mock.patch.object(sd.shutil, "which", return_value="git"), \
                    redirect_stdout(buf):
                sd.build_tar(os.path.join(tmp, "out.tar.gz"))
        finally:
            os.chdir(old_cwd)
        return buf.getvalue()

    def test_warns_on_untracked_new_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._build(tmp, ["backend/a.py"], ["backend/new_mod.py"])
        self.assertIn("untracked file NOT shipped", out)
        self.assertIn("backend/new_mod.py", out)
        self.assertIn("git add", out)

    def test_silent_when_nothing_untracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._build(tmp, ["backend/a.py"], [])
        self.assertNotIn("untracked", out)


# ---------------------------------------------------------------------------
# C-2: deploy API key via a 0600 file (E-4: container log cap)
# ---------------------------------------------------------------------------

class TestDeployApiKeyFile(unittest.TestCase):
    """audit 2026-10-23 C-2: the deploy API key must not appear in a
    remote command line or the container env — it travels SFTP into a
    0600 file that docker reads via --env-file and the health probes
    resolve with $(sed ...) on the server."""

    KEY = "test-key-123"

    def test_key_header_arg_resolves_from_file(self):
        with mock.patch.object(sd, "API_KEY", self.KEY), \
                mock.patch.object(sd, "KEY_FILE", "/home/u/.vdb_key"):
            arg = sd._key_header_arg()
            self.assertIn("/home/u/.vdb_key", arg)
            self.assertNotIn(self.KEY, arg)
            cmd = sd.health_wget()
            self.assertNotIn(self.KEY, cmd)
            self.assertIn("sed -n", cmd)

    def test_key_header_arg_empty_without_key(self):
        with mock.patch.object(sd, "API_KEY", ""):
            self.assertEqual(sd._key_header_arg(), "")
            self.assertNotIn("--header", sd.health_wget())

    def test_run_cmd_carries_env_file_not_key(self):
        with mock.patch.object(sd, "API_KEY", self.KEY), \
                mock.patch.object(sd, "KEY_FILE", "/home/u/.vdb_key"):
            api_key_arg = f"--env-file {sd.KEY_FILE} "
            cmd = sd.run_container_cmd("vllm-dashboard:X", api_key_arg)
            self.assertIn("--env-file /home/u/.vdb_key", cmd)
            self.assertNotIn(self.KEY, cmd)
            self.assertNotIn("VLLM_DASHBOARD_API_KEY=", cmd)

    def test_write_key_file_uploads_0600(self):
        c = mock.MagicMock()
        f = c.open_sftp.return_value.open.return_value.__enter__.return_value
        with mock.patch.object(sd, "API_KEY", self.KEY), \
                mock.patch.object(sd, "KEY_FILE", "/home/u/.vdb_key"):
            sd._write_key_file(c)
        f.write.assert_called_once_with(f"VLLM_DASHBOARD_API_KEY={self.KEY}\n")
        c.open_sftp.return_value.chmod.assert_called_once_with(
            "/home/u/.vdb_key", 0o600)

    def test_write_key_file_noop_without_key(self):
        c = mock.MagicMock()
        with mock.patch.object(sd, "API_KEY", ""):
            sd._write_key_file(c)
        c.open_sftp.assert_not_called()

    def test_run_cmd_caps_container_log(self):
        # audit 2026-10-23 E-4
        cmd = sd.run_container_cmd("vllm-dashboard:X", "")
        self.assertIn("--log-opt max-size=50m", cmd)
        self.assertIn("--log-opt max-file=5", cmd)


# ---------------------------------------------------------------------------
# A-1: zombie downgrade honors is_stopping
# ---------------------------------------------------------------------------

class TestZombieDowngradeStopGuard(unittest.TestCase):
    """audit 2026-10-23 A-1: the zombie downgrade (RUNNING + /health
    non-2xx) must not record a crash while a stop is in flight — the same
    guard the other two downgrade branches already carried."""

    def setUp(self):
        model_watchdog._crash_events.clear()
        model_watchdog._suspended.clear()
        model_watchdog._inflight.clear()
        model_watchdog._restart_tasks.clear()
        model_lifecycle._stopping_models.clear()
        self._sm = state_machine
        self._mid = self._sm.add_model(
            "b2-zombie", "b2-zombie", "/models/b2-zombie",
            config={"name": "b2-zombie", "path": "/models/b2-zombie"},
        ).id
        self._sm.set_model_locked(self._mid, True)
        self._sm.update_model_status(self._mid, ModelStatus.RUNNING)

    def tearDown(self):
        model_lifecycle._stopping_models.clear()
        model_watchdog._crash_events.clear()
        model_watchdog._suspended.clear()
        model_watchdog._inflight.clear()
        model_watchdog._restart_tasks.clear()
        self._sm.remove_model(self._mid)

    class _Client:
        async def get(self, url, **kwargs):
            if url.endswith("/v1/models"):
                return _ZombieResp(200, {"data": [{"id": "/models/b2-zombie"}]})
            return _ZombieResp(501)

    def _patch_remote(self):
        target = mock.MagicMock()
        target.base_url = "http://127.0.0.1:8000"
        target.port = 8000
        target.headers = {}
        self._saved = (
            model_service.get_http_client,
            model_service.resolve_api_target,
            model_status_sync._check_vllm_present_strict,
        )

        async def _no_presence(host, port):
            return False

        model_service.get_http_client = lambda: self._Client()
        model_service.resolve_api_target = lambda config: target
        # Defensive: the presence probe must never hit the real network
        # (tests/__init__.py forbids socket.create_connection).
        model_status_sync._check_vllm_present_strict = _no_presence

    def _restore_remote(self):
        (model_service.get_http_client,
         model_service.resolve_api_target,
         model_status_sync._check_vllm_present_strict) = self._saved

    def test_crash_recorded_without_stop_window(self):
        # control: the same zombie downgrade outside any stop IS a crash
        self._patch_remote()
        try:
            asyncio.run(model_status_sync._sync_status_once(timeout=3.0))
        finally:
            self._restore_remote()
        self.assertEqual(self._sm.get_model(self._mid).status, ModelStatus.STOPPED)
        self.assertIn(self._mid, model_watchdog._crash_events)

    def test_no_crash_while_stopping(self):
        model_lifecycle._stopping_models.add(self._mid)
        self._patch_remote()
        try:
            asyncio.run(model_status_sync._sync_status_once(timeout=3.0))
        finally:
            self._restore_remote()
        self.assertEqual(self._sm.get_model(self._mid).status, ModelStatus.STOPPED)
        self.assertNotIn(self._mid, model_watchdog._crash_events)


# ---------------------------------------------------------------------------
# A-2: a manual stop cancels the scheduled restart
# ---------------------------------------------------------------------------

class TestRestartTaskCancelled(unittest.TestCase):
    """audit 2026-10-23 A-2: a manual stop clears the crash history, but
    the scheduled 10 s restart used to still fire and re-launch the model
    the user just stopped — _clear must cancel the pending task."""

    def setUp(self):
        model_watchdog._crash_events.clear()
        model_watchdog._suspended.clear()
        model_watchdog._inflight.clear()
        model_watchdog._restart_tasks.clear()
        self._sm = state_machine
        self._mid = self._sm.add_model(
            "b2-cancel", "b2-cancel", "/models/b2-cancel",
            config={"name": "b2-cancel", "path": "/models/b2-cancel"},
        ).id
        self._sm.set_model_locked(self._mid, True)

    def tearDown(self):
        model_watchdog._crash_events.clear()
        model_watchdog._suspended.clear()
        model_watchdog._inflight.clear()
        model_watchdog._restart_tasks.clear()
        self._sm.remove_model(self._mid)

    def test_clear_cancels_pending_restart(self):
        async def scenario():
            model_watchdog.on_model_crash(self._mid)
            task = model_watchdog._restart_tasks.get(self._mid)
            self.assertIsNotNone(task)
            model_watchdog._clear(self._mid)
            await asyncio.sleep(0)
            self.assertTrue(task.cancelled())
            self.assertNotIn(self._mid, model_watchdog._restart_tasks)
        asyncio.run(scenario())

    def test_stop_watchdog_cancels_all_pending(self):
        async def scenario():
            model_watchdog.on_model_crash(self._mid)
            task = model_watchdog._restart_tasks.get(self._mid)
            self.assertIsNotNone(task)
            await model_watchdog.stop_watchdog()
            await asyncio.sleep(0)
            self.assertTrue(task.cancelled())
        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# C-1: oversized request bodies are rejected before buffering
# ---------------------------------------------------------------------------

class TestBodySizeGate(unittest.TestCase):
    """audit 2026-10-23 C-1: FastAPI/Pydantic buffer the whole body in
    memory — a multi-GB JSON from an (authenticated) LAN client would OOM
    the container. The gate must 413 on the Content-Length header alone,
    without reading the body."""

    LIMIT = 64 * 1024 * 1024

    @staticmethod
    def _request(method: str, content_length: int | None) -> Request:
        headers = []
        if content_length is not None:
            headers.append((b"content-length", str(content_length).encode()))
        scope = {
            "type": "http",
            "method": method,
            "path": "/api/v1/models",
            "query_string": b"",
            "headers": headers,
            "state": {},
        }
        return Request(scope)

    @staticmethod
    async def _pass_through(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    def _gate(self, request: Request) -> JSONResponse:
        async def scenario():
            return await body_size_gate_middleware(request, self._pass_through)
        return asyncio.run(scenario())

    def test_oversized_body_is_413(self):
        resp = self._gate(self._request("POST", self.LIMIT + 1))
        self.assertEqual(resp.status_code, 413)
        self.assertIn(b"payload_too_large", resp.body)

    def test_exact_limit_is_413(self):
        # the gate is "at or above" the cap (audit: ≥64MB → 413)
        resp = self._gate(self._request("POST", self.LIMIT))
        self.assertEqual(resp.status_code, 413)
        self.assertIn(b"payload_too_large", resp.body)

    def test_just_below_limit_passes(self):
        resp = self._gate(self._request("POST", self.LIMIT - 1))
        self.assertEqual(resp.status_code, 200)

    def test_missing_content_length_passes(self):
        resp = self._gate(self._request("POST", None))
        self.assertEqual(resp.status_code, 200)

    def test_get_with_length_passes(self):
        resp = self._gate(self._request("GET", 10 * self.LIMIT))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# C-3: HF token out of the remote command line
# ---------------------------------------------------------------------------

class TestHfTokenNotInCommandLine(unittest.TestCase):
    """audit 2026-10-23 C-3: the HF token must not sit in the remote
    command line (ps-visible on the single-user host) — it travels the
    channel stdin into a 0600 temp file that the wrapper / curl read."""

    TOKEN = "hf_token_secret_123"

    def _executor(self):
        calls: list[tuple[str, object]] = []

        class Fake(ModelOps):
            # the escape helper normally comes from RemoteExecutor (the
            # mixin is tested bare — same stub as tests/test_hf_token.py)
            _DQ_ESCAPE_RE = RemoteExecutor._DQ_ESCAPE_RE
            _escape_double_quoted = RemoteExecutor._escape_double_quoted

            def execute(self, command, timeout=None, stdin_data=None):
                calls.append((command, stdin_data))
                return {"success": True, "stdout": "12345", "stderr": ""}

            def _get_activate_cmd(self):
                return ""

        return Fake(), calls

    def _patch_token(self, token: str):
        class _Cfg:
            hf_token = token

        class _Mgr:
            def get_config(self):
                return _Cfg()

        return mock.patch.object(remote_client, "config_manager", _Mgr())

    def test_download_token_travels_stdin_not_cmdline(self):
        ex, calls = self._executor()
        with self._patch_token(self.TOKEN):
            ex.download_model("org/repo")
        cmd, stdin_data = calls[0]
        self.assertNotIn(self.TOKEN, cmd)
        self.assertIn("VDB_TOK", cmd)
        self.assertIn("chmod 600", cmd)
        self.assertIn("hf download", cmd)
        self.assertEqual(stdin_data, self.TOKEN)

    def test_download_without_token_sends_no_stdin(self):
        ex, calls = self._executor()
        with self._patch_token(""):
            ex.download_model("org/repo")
        cmd, stdin_data = calls[0]
        self.assertIsNone(stdin_data)
        self.assertNotIn("VDB_TOK", cmd)
        self.assertNotIn("HF_TOKEN", cmd)

    def test_total_size_token_travels_stdin_not_cmdline(self):
        ex, calls = self._executor()
        with self._patch_token(self.TOKEN):
            ex.get_download_total_size("org/repo")
        cmd, stdin_data = calls[0]
        self.assertNotIn(self.TOKEN, cmd)
        self.assertIn("VDB_TOK", cmd)
        self.assertIn("--config", cmd)
        self.assertEqual(stdin_data, self.TOKEN)

    def test_total_size_without_token_plain_curl(self):
        ex, calls = self._executor()
        with self._patch_token(""):
            ex.get_download_total_size("org/repo")
        cmd, stdin_data = calls[0]
        self.assertIsNone(stdin_data)
        self.assertNotIn("VDB_TOK", cmd)
        self.assertNotIn("--config", cmd)
