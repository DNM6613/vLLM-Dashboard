"""Audit 2026-10-22 batch 1 regressions.

BCK-1 zombie-engine self-heal: the status-sync /health downgrade, the
watchdog force-kill branch, and the chat precheck fail-fast.
REM-1: the strict-mode first SSH connect must not deadlock (the
missing_host_key callback fires synchronously in the connect() thread and
re-takes the non-reentrant known_hosts lock).
BCK-2 residue: the wedged shared probe thread is restarted past the stale
bound, and the refresh=True start path is wall-clock bounded.
"""
import tempfile  # noqa: I001 — the backend.service imports below must stay
# in two separate lines (see the order note there); ruff would merge them
# into one and break the facade import cycle.
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import paramiko
from fastapi import HTTPException

from backend.config.remote_client import ApiTarget, config_manager
from backend.config.server_config import ServerConfig
from backend.executor.remote import ssh as ssh_mod
from backend.executor.remote import ssh_probe as probe
from backend.schemas.model import ModelStatus
# Import order matters: model_chat ↔ model_service form a facade cycle that
# only resolves when model_service is imported first.
from backend.service import model_service
from backend.service import model_chat, model_lifecycle, model_watchdog


def _target_guard(config):
    return ApiTarget(
        base_url=f"http://{config.host}:8000", port=8000, headers={}, api_key=None)


def _reset_watchdog():
    model_watchdog._crash_events.clear()
    model_watchdog._suspended.clear()
    model_watchdog._inflight.clear()


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeVllmClient:
    """/v1/models always lists the model (the static-endpoint zombie
    behavior); /health follows the configured behavior (int = status code,
    None = network-level failure)."""

    def __init__(self, health):
        self._health = health

    async def get(self, url, timeout=None, headers=None):
        if url.endswith("/v1/models"):
            return _FakeResp(200, {"data": [{"id": "/models/m1"}]})
        if self._health is None:
            raise RuntimeError("network unreachable")
        return _FakeResp(self._health, {"status": "ok"})


class TestZombieEngineSync(unittest.IsolatedAsyncioTestCase):
    async def _sync(self, health):
        updates, crashed = [], []
        fake_model = SimpleNamespace(
            id="m1", name="m1", path="/models/m1",
            status=ModelStatus.RUNNING, locked=True, loading_since=None)
        with patch.object(model_service.state_machine, "get_all_models",
                          return_value=[fake_model]), \
             patch.object(model_service.state_machine, "update_model_status",
                          side_effect=lambda mid, st: updates.append((mid, st)) or True), \
             patch.object(model_service.state_machine, "clear_current_model",
                          return_value=True), \
             patch.object(model_service, "_watchdog_on_crash",
                          side_effect=crashed.append), \
             patch.object(model_service.config_manager, "get_config",
                          return_value=ServerConfig(host="10.131.1.7")), \
             patch.object(model_service, "get_http_client",
                          return_value=_FakeVllmClient(health)), \
             patch.object(model_service, "resolve_api_target",
                          side_effect=_target_guard):
            changed = await model_service._sync_status_once(timeout=2.0)
        return changed, updates, crashed

    async def test_zombie_downgrades_running_and_records_crash(self):
        # /v1/models still lists the model (static endpoint) while /health
        # answers 501 — the 2026-10-22 01:19 zombie shape.
        changed, updates, crashed = await self._sync(health=501)
        self.assertEqual(changed, 1)
        self.assertIn(("m1", ModelStatus.STOPPED), updates)
        self.assertEqual(crashed, ["m1"])

    async def test_healthy_engine_keeps_running(self):
        changed, _updates, crashed = await self._sync(health=200)
        self.assertEqual(changed, 0)
        self.assertEqual(crashed, [])

    async def test_network_failure_keeps_running_conservatively(self):
        changed, _updates, crashed = await self._sync(health=None)
        self.assertEqual(changed, 0)
        self.assertEqual(crashed, [])


class TestZombieWatchdogBranch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_watchdog()

    async def _attempt(self, health):
        killed, started = [], []
        fake_model = SimpleNamespace(
            id="m1", name="m1", path="/models/m1",
            locked=True, status=ModelStatus.STOPPED)
        executor = MagicMock()
        executor.stop_vllm.side_effect = (
            lambda force, model_path: killed.append((force, model_path))
            or {"success": True, "error": None, "mode": "scoped"})

        async def fake_start(mid):
            started.append(mid)
            return {"status": "loading", "model_id": mid,
                    "command": "vllm serve /models/m1"}

        class _FakeConsoleMgr:
            def get_or_create(self, cfg):
                return None, False

        with patch.object(model_watchdog.state_machine, "get_model",
                          return_value=fake_model), \
             patch.object(model_service, "_check_vllm_present_strict",
                          new=AsyncMock(return_value=True)), \
             patch.object(model_service, "_check_vllm_engine_health",
                          new=AsyncMock(return_value=health)), \
             patch.object(model_service, "get_remote_executor",
                          return_value=executor), \
             patch.object(model_service, "start_model", side_effect=fake_start), \
             patch.object(model_service, "console_session_manager",
                          _FakeConsoleMgr()), \
             patch.object(model_service.config_manager, "get_config",
                          return_value=ServerConfig(host="10.131.1.7")), \
             patch.object(model_service, "resolve_api_target",
                          side_effect=_target_guard):
            await model_watchdog._attempt_restart("m1")
        return killed, started

    async def test_zombie_is_force_killed_then_restarted(self):
        killed, started = await self._attempt(health=False)
        self.assertEqual(killed, [(True, "/models/m1")])
        self.assertEqual(started, ["m1"])

    async def test_healthy_process_still_skips_start(self):
        killed, started = await self._attempt(health=True)
        self.assertEqual(killed, [])
        self.assertEqual(started, [])


class TestChatPrecheckZombie(unittest.IsolatedAsyncioTestCase):
    async def test_zombie_engine_fails_fast_503(self):
        fake_model = SimpleNamespace(id="m1", name="m1", path="/models/m1",
                                     status=ModelStatus.RUNNING)

        class _ModelsOnlyClient:
            async def get(self, url, timeout=None, headers=None):
                return _FakeResp(200, {"data": [{"id": "/models/m1"}]})

        with patch.object(model_service.state_machine, "get_model",
                          return_value=fake_model), \
             patch.object(model_service.config_manager, "get_config",
                          return_value=ServerConfig(host="10.131.1.7")), \
             patch.object(model_service, "get_http_client",
                          return_value=_ModelsOnlyClient()), \
             patch.object(model_service, "resolve_api_target",
                          side_effect=_target_guard), \
             patch.object(model_service, "_check_vllm_engine_health",
                          new=AsyncMock(return_value=False)), \
             self.assertRaises(HTTPException) as ctx:
            await model_chat.chat_precheck("m1")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("引擎不健康", str(ctx.exception.detail))


class TestStrictFirstConnect(unittest.TestCase):
    def test_first_connect_completes_and_persists_host(self):
        with tempfile.TemporaryDirectory() as td:
            known_hosts = str(Path(td) / "known_hosts")

            class FakeSock:
                def settimeout(self, _v):
                    pass

                def setsockopt(self, *a, **k):
                    pass

            saved = []

            class FakeKey:
                def get_name(self):
                    return "ssh-ed25519"

            class FakeHostKeys:
                def add(self, host, name, key):
                    pass

                def save(self, path):
                    saved.append(path)

            class FakeClient:
                def __init__(self):
                    self._hk = FakeHostKeys()
                    self._policy = None

                def load_host_keys(self, _path):
                    pass

                def set_missing_host_key_policy(self, p):
                    self._policy = p

                def get_host_keys(self):
                    return self._hk

                def connect(self, host, **_kw):
                    # paramiko 5.0.0 semantics: the policy callback fires
                    # synchronously in the connect() call thread
                    # (paramiko/client.py L448) — exactly the path that
                    # deadlocked before the REM-1 fix.
                    if self._policy is not None:
                        self._policy.missing_host_key(self, FakeKey(), host)

                def get_transport(self):
                    return None

            result = {}

            def run():
                result["client"] = ssh_mod.build_ssh_client(
                    "10.99.99.99", 2222, "user", password="pw")

            t = threading.Thread(target=run)
            with patch.object(ssh_mod.settings, "SSH_STRICT_HOST_KEY", True), \
                 patch.object(ssh_mod, "_known_hosts_path",
                              return_value=known_hosts), \
                 patch.object(ssh_mod.socket, "create_connection",
                              return_value=FakeSock()), \
                 patch.object(paramiko, "SSHClient", FakeClient):
                t.start()
                t.join(timeout=5)
            # Before the fix this hung forever: the callback re-takes the
            # non-reentrant lock that was held across the handshake.
            self.assertFalse(
                t.is_alive(),
                "deadlock: strict first connect never returned (REM-1)")
            self.assertIn("client", result)
            self.assertEqual(saved, [known_hosts])


class TestProbeThreadSelfHeal(unittest.TestCase):
    def setUp(self):
        self._state = {
            "cache": probe._ssh_probe_cache,
            "fp": probe._ssh_probe_fp,
            "ts": probe._ssh_probe_ts,
            "restart_ts": probe._probe_restart_ts,
            "warn_ts": probe._stale_warn_ts,
            "thread": probe._ssh_probe_thread,
        }
        probe._ssh_probe_stop.clear()

    def tearDown(self):
        probe._ssh_probe_stop.set()
        if probe._ssh_probe_thread is not None:
            probe._ssh_probe_thread.join(timeout=5)
        probe._ssh_probe_cache = self._state["cache"]
        probe._ssh_probe_fp = self._state["fp"]
        probe._ssh_probe_ts = self._state["ts"]
        probe._probe_restart_ts = self._state["restart_ts"]
        probe._stale_warn_ts = self._state["warn_ts"]
        probe._ssh_probe_thread = self._state["thread"]
        probe._ssh_probe_stop.clear()

    def test_stale_cache_restarts_wedged_thread(self):
        cfg = SimpleNamespace(host="10.99.99.99", ssh_port=22, ssh_username="u",
                              ssh_password="p", ssh_key_path="")
        probe._ssh_probe_cache = {"connected": True, "error": None, "latency_ms": 1}
        probe._ssh_probe_fp = ("10.99.99.99", 22, "u", "p", "")
        probe._ssh_probe_ts = time.monotonic() - 120.0
        probe._probe_restart_ts = 0.0
        probe._stale_warn_ts = 0.0

        # A wedged thread: alive (so is_alive() alone never rebuilds) and
        # obeying the module stop signal exactly like the real probe loop.
        release = threading.Event()

        def wedged():
            while not probe._ssh_probe_stop.is_set():
                if release.wait(timeout=0.05):
                    return

        old = threading.Thread(target=wedged, daemon=True)
        old.start()
        probe._ssh_probe_thread = old

        try:
            with patch.object(probe, "_probe_once",
                              return_value={"connected": False,
                                            "error": "mock", "latency_ms": 1}), \
                 patch.object(config_manager, "get_config", return_value=cfg):
                out = probe.check_ssh_connectivity(refresh=False)
                new_thread = probe._ssh_probe_thread
            self.assertTrue(out["connected"])  # serves the old cache value
            self.assertIsNotNone(new_thread)
            self.assertIsNot(new_thread, old)
            self.assertTrue(new_thread.is_alive())
            # The old thread saw the stop signal and exited on its own —
            # the same exit path as the real _ssh_probe_loop.
            old.join(timeout=2)
            self.assertFalse(old.is_alive())
        finally:
            release.set()


class TestStartProbeWallClock(unittest.IsolatedAsyncioTestCase):
    async def test_wedged_probe_times_out_to_503(self):
        fake_model = SimpleNamespace(id="m1", name="m1", path="/models/m1",
                                     status=ModelStatus.STOPPED, locked=False)

        def blocking_probe(refresh=False):
            time.sleep(3)
            return {"connected": True}

        with patch.object(model_service.state_machine, "get_model",
                          return_value=fake_model), \
             patch.object(model_service, "check_ssh_connectivity",
                          side_effect=blocking_probe), \
             patch.object(model_service.settings, "SSH_CMD_TIMEOUT", 0.2), \
             self.assertRaises(HTTPException) as ctx:
            await model_lifecycle._start_model("m1")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("probe itself timed out", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
