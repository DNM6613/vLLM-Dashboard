"""Model lock watchdog tests (plan A: crash-repair-only auto-restart).

Covers the single-lock mutex, crash → auto-restart via the watchdog tick,
breaker suspension (3 real crashes in 10 min → FAILED, no restart; synthetic
replay events do NOT count), manual-stop semantics (locked + STOPPED without
a crash event is NOT started), the pre-restart replay (locked model RUNNING
before a dashboard restart, replayed as a synthetic event), the P2-1 remote
presence guard (no duplicate start command while the remote process lives),
the deferred-restart branches (409 clear / 400 no-command FAILED / other
defer), the 10 s delayed-task path, the A-1 tick sync gate, the start-probe
rollback re-arming (a locked model's silently failed start must re-arm the
watchdog, an unlocked one must not), and the HTTP surface of
POST /api/v1/models/lock.
"""
import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from backend.controller.state_machine import StateMachine, state_machine
from backend.schemas.model import ModelStatus
from backend.service import model_service, model_watchdog


def _reset_watchdog():
    model_watchdog._crash_events.clear()
    model_watchdog._suspended.clear()
    model_watchdog._inflight.clear()


class TestModelLock(unittest.TestCase):
    def setUp(self):
        _reset_watchdog()
        self._sm = state_machine
        self._ids = [
            self._sm.add_model(
                name, name, f"/models/{name}",
                config={"name": name, "path": f"/models/{name}"},
            ).id
            for name in ("lock-a", "lock-b")
        ]
        self._started: list[str] = []
        self._sent_commands: list[str] = []
        self._orig_start = model_service.start_model
        self._orig_sync = model_service._sync_status_once
        self._orig_present = model_service._check_vllm_present_strict
        self._orig_console = model_service.console_session_manager

        async def fake_start(model_id):
            self._started.append(model_id)
            self._sm.update_model_status(model_id, ModelStatus.LOADING)
            return {"status": "loading", "model_id": model_id,
                    "command": f"vllm serve /models/{model_id}"}

        async def no_sync(timeout):
            return 0

        async def unknown_present(host, port):
            return None

        sent = self._sent_commands

        class _FakeSession:
            def send_command(self, cmd):
                sent.append(cmd)
                return True

        class _FakeConsoleManager:
            def get_or_create(self, config):
                return _FakeSession(), True

        model_service.start_model = fake_start
        model_service._sync_status_once = no_sync
        model_service._check_vllm_present_strict = unknown_present
        model_service.console_session_manager = _FakeConsoleManager()

    def tearDown(self):
        model_service.start_model = self._orig_start
        model_service._sync_status_once = self._orig_sync
        model_service._check_vllm_present_strict = self._orig_present
        model_service.console_session_manager = self._orig_console
        _reset_watchdog()
        for mid in self._ids:
            self._sm.remove_model(mid)

    def test_single_lock_exclusive(self):
        a, b = self._ids
        self._sm.set_model_locked(a, True)
        self.assertTrue(self._sm.get_model(a).locked)
        unlocked = self._sm.set_model_locked(b, True)
        self.assertEqual(unlocked, [a])
        self.assertTrue(self._sm.get_model(b).locked)
        self.assertFalse(self._sm.get_model(a).locked)
        self._sm.set_model_locked(b, False)
        self.assertFalse(self._sm.get_model(b).locked)

    def test_crash_event_triggers_restart_on_tick(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        self._sm.update_model_status(mid, ModelStatus.RUNNING)
        # sync order: the STOPPED transition happens before the crash hook
        self._sm.update_model_status(mid, ModelStatus.STOPPED)
        model_watchdog.on_model_crash(mid)
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertEqual(self._started, [mid])
        # model is now LOADING; the next tick clears the consumed event
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertNotIn(mid, model_watchdog._crash_events)
        self.assertEqual(self._started, [mid])  # no double start

    def test_watchdog_delivers_start_command_to_console(self):
        # Incident 2026-10-17 10:15: the watchdog path has no client to send
        # the start command (the browser flow does it via the console WS), so
        # the backend must deliver it to the console PTY itself — otherwise
        # every auto-start sets LOADING and the 10 s probe finds no vLLM
        # process, rolling back and retrying forever with the model never up.
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        self._sm.update_model_status(mid, ModelStatus.RUNNING)
        self._sm.update_model_status(mid, ModelStatus.STOPPED)  # crash
        model_watchdog.on_model_crash(mid)
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertEqual(self._started, [mid])
        # the exact start command (with a trailing newline) was sent to the
        # console session so the remote shell actually launches vLLM
        self.assertEqual(self._sent_commands, [f"vllm serve /models/{mid}\n"])

    def test_three_crashes_suspends_and_marks_failed(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        for _ in range(3):
            self._sm.update_model_status(mid, ModelStatus.RUNNING)
            self._sm.update_model_status(mid, ModelStatus.STOPPED)
            model_watchdog.on_model_crash(mid)
        self.assertTrue(model_watchdog.is_suspended(mid))
        model = self._sm.get_model(mid)
        self.assertEqual(model.status, ModelStatus.FAILED)
        self.assertIn("自动启动已暂停", model.error_message)
        # suspended model: the tick must not restart it
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertEqual(self._started, [])

    def test_manual_stop_not_restarted(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        # locked + STOPPED with no crash event (manual stop) → tick stays idle
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertEqual(self._started, [])

    def test_recovery_clears_suspension(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        model_watchdog._suspended.add(mid)
        model_watchdog._crash_events[mid] = [(datetime.now(), False)]
        self._sm.update_model_status(mid, ModelStatus.LOADING)  # manual start
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertFalse(model_watchdog.is_suspended(mid))
        self.assertNotIn(mid, model_watchdog._crash_events)
        self.assertEqual(self._started, [])

    def test_synthetic_replay_events_do_not_trip_breaker(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        # 3 synthetic (dashboard-restart replay) events → no suspension
        for _ in range(3):
            model_watchdog.on_model_crash(mid, synthetic=True)
        self.assertFalse(model_watchdog.is_suspended(mid))
        # mixed window: 2 synthetic + 2 real → real count (2) below the limit
        model_watchdog._crash_events.clear()
        for _ in range(2):
            model_watchdog.on_model_crash(mid, synthetic=True)
        for _ in range(2):
            model_watchdog.on_model_crash(mid)
        self.assertFalse(model_watchdog.is_suspended(mid))
        # 3 real crashes still trip the breaker
        model_watchdog._crash_events.clear()
        for _ in range(3):
            model_watchdog.on_model_crash(mid)
        self.assertTrue(model_watchdog.is_suspended(mid))

    def test_start_watchdog_replay_is_synthetic(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        self._sm.update_model_status(mid, ModelStatus.RUNNING)
        calls: list[tuple[str, bool]] = []
        real = model_watchdog.on_model_crash

        def spy(model_id, synthetic=False):
            calls.append((model_id, synthetic))
            real(model_id, synthetic=synthetic)

        model_watchdog.on_model_crash = spy
        self._sm._pending_locked_restarts = [mid]
        try:
            async def drive():
                await model_watchdog.start_watchdog()
                await model_watchdog.stop_watchdog()
            asyncio.run(drive())
        finally:
            model_watchdog.on_model_crash = real
        self.assertEqual(calls, [(mid, True)])

    def test_delayed_restart_task_fires(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        orig_delay = model_watchdog.RESTART_DELAY_SECONDS
        model_watchdog.RESTART_DELAY_SECONDS = 0.05
        try:
            async def drive():
                model_watchdog.on_model_crash(mid)  # in-loop: create_task path
                await asyncio.sleep(0.3)
            asyncio.run(drive())
        finally:
            model_watchdog.RESTART_DELAY_SECONDS = orig_delay
        self.assertEqual(self._started, [mid])

    def test_attempt_restart_409_clears_event(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)

        async def start_409(model_id):
            raise HTTPException(status_code=409, detail="Model is already running")

        model_service.start_model = start_409
        model_watchdog.on_model_crash(mid)
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertNotIn(mid, model_watchdog._crash_events)
        self.assertEqual(self._started, [])

    def test_attempt_restart_400_no_command_marks_failed_and_clears(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)

        async def start_400(model_id):
            raise HTTPException(status_code=400, detail="Start command not configured")

        model_service.start_model = start_400
        model_watchdog.on_model_crash(mid)
        asyncio.run(model_watchdog.watchdog_tick())
        model = self._sm.get_model(mid)
        self.assertEqual(model.status, ModelStatus.FAILED)
        self.assertIn(model_watchdog.NO_START_COMMAND_MESSAGE, model.error_message)
        self.assertNotIn(mid, model_watchdog._crash_events)
        self.assertEqual(self._started, [])

    def test_attempt_restart_defers_on_other_error(self):
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)

        async def start_503(model_id):
            raise HTTPException(status_code=503, detail="AI server unreachable")

        model_service.start_model = start_503
        model_watchdog.on_model_crash(mid)
        asyncio.run(model_watchdog.watchdog_tick())
        # deferred: the event stays so the next tick retries
        self.assertIn(mid, model_watchdog._crash_events)
        self.assertEqual(self._sm.get_model(mid).status, ModelStatus.STOPPED)
        self.assertEqual(self._started, [])

    def test_attempt_restart_skips_when_remote_process_present(self):
        # P2-1 regression lock: the original vLLM process is still alive on
        # the remote → the start command must NOT be re-delivered.
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)

        async def present(host, port):
            return True

        model_service._check_vllm_present_strict = present
        model_watchdog.on_model_crash(mid)
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertEqual(self._started, [])
        # event kept (not consumed) — the tick's sync reconciles and clears it
        self.assertIn(mid, model_watchdog._crash_events)

    def test_tick_skips_sync_when_nothing_locked(self):
        # A-1 regression lock: no locked model and no pending event → the
        # tick must not touch the remote at all.
        calls: list[int] = []

        async def spy_sync(timeout):
            calls.append(1)
            return 0

        model_service._sync_status_once = spy_sync
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertEqual(calls, [])
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertEqual(calls, [1])

    def test_probe_rollback_re_arms_watchdog_when_locked(self):
        # Incident 2026-10-17: a locked model's start command is never
        # delivered (console PTY not ready), the probe rolls back to
        # STOPPED, and the crash event must be re-armed — otherwise the
        # watchdog is orphaned and never retries.
        mid = self._ids[0]
        self._sm.set_model_locked(mid, True)
        crashes: list[str] = []
        orig_on_crash = model_service._watchdog_on_crash
        orig_delay = model_service._START_PROBE_DELAY
        async def gone(host, port):
            return False
        model_service._watchdog_on_crash = crashes.append
        model_service._START_PROBE_DELAY = 0.01
        model_service._check_vllm_present_strict = gone
        try:
            async def drive():
                self._sm.update_model_status(mid, ModelStatus.LOADING)
                await model_service._verify_start_probe(mid, "127.0.0.1", 8000)
            asyncio.run(drive())
        finally:
            model_service._watchdog_on_crash = orig_on_crash
            model_service._START_PROBE_DELAY = orig_delay
        self.assertEqual(self._sm.get_model(mid).status, ModelStatus.STOPPED)
        self.assertEqual(crashes, [mid])
        # the re-armed event schedules a retry: after the delay the model
        # is started again (the fake start leaves it in LOADING)
        orig_delay2 = model_watchdog.RESTART_DELAY_SECONDS
        model_watchdog.RESTART_DELAY_SECONDS = 0.05
        try:
            async def drive2():
                model_watchdog.on_model_crash(mid)
                await asyncio.sleep(0.3)
            asyncio.run(drive2())
        finally:
            model_watchdog.RESTART_DELAY_SECONDS = orig_delay2
            model_watchdog._restart_tasks.discard(None)
            model_watchdog._restart_tasks.clear()
        self.assertEqual(self._started, [mid])
        self.assertEqual(self._sm.get_model(mid).status, ModelStatus.LOADING)

    def test_probe_rollback_no_watchdog_when_unlocked(self):
        # Manual start (not locked) fails silently → the rollback must NOT
        # re-arm the watchdog: a failed manual start is not a crash event.
        mid = self._ids[0]
        crashes: list[str] = []
        orig_on_crash = model_service._watchdog_on_crash
        orig_delay = model_service._START_PROBE_DELAY
        async def gone(host, port):
            return False
        model_service._watchdog_on_crash = crashes.append
        model_service._START_PROBE_DELAY = 0.01
        model_service._check_vllm_present_strict = gone
        try:
            async def drive():
                self._sm.update_model_status(mid, ModelStatus.LOADING)
                await model_service._verify_start_probe(mid, "127.0.0.1", 8000)
            asyncio.run(drive())
        finally:
            model_service._watchdog_on_crash = orig_on_crash
            model_service._START_PROBE_DELAY = orig_delay
        self.assertEqual(self._sm.get_model(mid).status, ModelStatus.STOPPED)
        self.assertEqual(crashes, [])
        self.assertEqual(self._started, [])

    def test_pre_restart_running_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "models_state.json"
            sm = StateMachine(state_file=str(state_file))
            mid = sm.add_model(
                "lock-pre", "lock-pre", "/models/lock-pre",
                config={"name": "lock-pre", "path": "/models/lock-pre"},
            ).id
            sm.set_model_locked(mid, True)
            sm.update_model_status(mid, ModelStatus.RUNNING)
            sm._flush_state()
            reloaded = StateMachine(state_file=str(state_file))
            self.assertEqual(reloaded.take_pending_locked_restarts(), [mid])
            self.assertEqual(reloaded.take_pending_locked_restarts(), [])
            self.assertEqual(reloaded.get_model(mid).status, ModelStatus.STOPPED)


class _FakeLaunchConfig:
    def __init__(self, with_command: bool):
        self._with_command = with_command

    def load_config(self, model_id):
        if not self._with_command:
            return None
        return {"start_command": "vllm serve /models/lock-a", "env_vars": ""}


class TestLockEndpointHTTP(unittest.TestCase):
    """HTTP surface of POST /api/v1/models/lock (audit 2026-10-17 A-2)."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from backend.main import app

        self._client = TestClient(app)
        self._ids = [
            state_machine.add_model(
                name, name, f"/models/{name}",
                config={"name": name, "path": f"/models/{name}"},
            ).id
            for name in ("http-a", "http-b")
        ]
        self._orig_lcm = model_watchdog.launch_config_manager

    def tearDown(self):
        model_watchdog.launch_config_manager = self._orig_lcm
        _reset_watchdog()
        for mid in self._ids:
            state_machine.remove_model(mid)

    def _lock(self, model_id: str, locked: bool):
        return self._client.post(
            "/api/v1/models/lock", params={"model_id": model_id, "locked": locked}
        )

    def test_unknown_model_404(self):
        r = self._lock("ghost-model", True)
        self.assertEqual(r.status_code, 404)
        self.assertIn("not found", str(r.json()).lower())

    def test_unlock_unknown_model_404(self):
        r = self._lock("ghost-model", False)
        self.assertEqual(r.status_code, 404)

    def test_lock_without_start_command_400(self):
        model_watchdog.launch_config_manager = _FakeLaunchConfig(with_command=False)
        mid = self._ids[0]
        r = self._lock(mid, True)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Start command", str(r.json()))
        self.assertFalse(state_machine.get_model(mid).locked)

    def test_lock_mutex_and_unlock_200(self):
        model_watchdog.launch_config_manager = _FakeLaunchConfig(with_command=True)
        a, b = self._ids
        r = self._lock(a, True)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body.get("locked"), True)
        self.assertEqual(body.get("unlocked"), [])
        r = self._lock(b, True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("unlocked"), [a])
        self.assertFalse(state_machine.get_model(a).locked)
        self.assertTrue(state_machine.get_model(b).locked)
        r = self._lock(b, False)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("locked"), False)
        self.assertFalse(state_machine.get_model(b).locked)


if __name__ == "__main__":
    unittest.main()
