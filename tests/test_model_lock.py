"""Model lock watchdog tests (plan A: crash-repair-only auto-restart).

Covers the single-lock mutex, crash → auto-restart via the watchdog tick,
breaker suspension (3 crashes in 10 min → FAILED, no restart), manual-stop
semantics (locked + STOPPED without a crash event is NOT started), and the
pre-restart replay (locked model RUNNING before a dashboard restart).
"""
import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

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
        self._orig_start = model_service.start_model
        self._orig_sync = model_service._sync_status_once

        async def fake_start(model_id):
            self._started.append(model_id)
            self._sm.update_model_status(model_id, ModelStatus.LOADING)
            return {"status": "loading", "model_id": model_id, "command": ""}

        async def no_sync(timeout):
            return 0

        model_service.start_model = fake_start
        model_service._sync_status_once = no_sync

    def tearDown(self):
        model_service.start_model = self._orig_start
        model_service._sync_status_once = self._orig_sync
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
        model_watchdog._crash_events[mid] = [datetime.now()]
        self._sm.update_model_status(mid, ModelStatus.LOADING)  # manual start
        asyncio.run(model_watchdog.watchdog_tick())
        self.assertFalse(model_watchdog.is_suspended(mid))
        self.assertNotIn(mid, model_watchdog._crash_events)
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


if __name__ == "__main__":
    unittest.main()
