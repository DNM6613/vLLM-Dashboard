"""Model card "last started" timestamp (last_started_at).

Covers the recording point (the single LOADING transition shared by all
three start paths — manual start, stop-then-start switch, watchdog
relaunch), its invariants (LOADING re-entry does not overwrite it, later
stop/failure does not clear it), persistence across a dashboard restart
(the transient-state reset must not drop it), and the HTTP start path.
"""
import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from backend.config.remote_client import ApiTarget
from backend.config.server_config import ServerConfig
from backend.controller.state_machine import StateMachine
from backend.schemas.model import ModelStatus
from backend.service import model_service

LAUNCH_CFG = {"start_command": "vllm serve m1", "env_vars": ""}


def _resolve_target(config):
    return ApiTarget(
        base_url=f"http://{config.host}:8000",
        port=8000,
        headers={},
        api_key=None,
    )


def _make_sm(tmp: str) -> StateMachine:
    return StateMachine(state_file=str(Path(tmp) / "models_state.json"))


def _add_model(sm: StateMachine, name: str = "m1") -> str:
    return sm.add_model(
        name, name, f"/models/{name}",
        config={"name": name, "path": f"/models/{name}"},
    ).id


class TestLastStartedAtRecording(unittest.TestCase):
    def test_transition_into_loading_records_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            sm = _make_sm(tmp)
            mid = _add_model(sm)
            self.assertIsNone(sm.get_model(mid).last_started_at)
            ts = datetime(2026, 10, 29, 23, 24, 24)
            sm.update_model_status(mid, ModelStatus.LOADING, now=ts)
            model = sm.get_model(mid)
            self.assertEqual(model.last_started_at, ts)
            self.assertEqual(model.loading_since, ts)

    def test_loading_reentry_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            sm = _make_sm(tmp)
            mid = _add_model(sm)
            first = datetime(2026, 10, 29, 23, 24, 24)
            sm.update_model_status(mid, ModelStatus.LOADING, now=first)
            sm.update_model_status(mid, ModelStatus.LOADING,
                                   now=datetime(2026, 10, 30, 1, 0, 0))
            self.assertEqual(sm.get_model(mid).last_started_at, first)

    def test_stop_failure_run_do_not_clear_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            sm = _make_sm(tmp)
            mid = _add_model(sm)
            ts = datetime(2026, 10, 29, 23, 24, 24)
            sm.update_model_status(mid, ModelStatus.LOADING, now=ts)
            for status in (ModelStatus.RUNNING, ModelStatus.FAILED,
                           ModelStatus.STOPPED):
                sm.update_model_status(mid, status)
                self.assertEqual(sm.get_model(mid).last_started_at, ts)
            # loading_since is cleared on exit from LOADING, last_started_at
            # is not — the two fields must not be conflated.
            self.assertIsNone(sm.get_model(mid).loading_since)

    def test_new_restart_overwrites_previous_start_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            sm = _make_sm(tmp)
            mid = _add_model(sm)
            sm.update_model_status(mid, ModelStatus.LOADING,
                                   now=datetime(2026, 10, 29, 1, 0, 0))
            sm.update_model_status(mid, ModelStatus.STOPPED)
            second = datetime(2026, 10, 29, 2, 0, 0)
            sm.update_model_status(mid, ModelStatus.LOADING, now=second)
            self.assertEqual(sm.get_model(mid).last_started_at, second)


class TestLastStartedAtPersistence(unittest.TestCase):
    def test_survives_reload_and_transient_state_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "models_state.json"
            sm = StateMachine(state_file=str(state_file))
            mid = _add_model(sm, "persist-a")
            ts = datetime(2026, 10, 29, 23, 24, 24)
            sm.update_model_status(mid, ModelStatus.LOADING, now=ts)
            sm.update_model_status(mid, ModelStatus.RUNNING)
            sm._flush_state()
            reloaded = StateMachine(state_file=str(state_file))
            model = reloaded.get_model(mid)
            # RUNNING is transient: reset to STOPPED on restart ...
            self.assertEqual(model.status, ModelStatus.STOPPED)
            # ... but the last start timestamp must survive.
            self.assertEqual(model.last_started_at, ts)

    def test_model_never_started_stays_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "models_state.json"
            sm = StateMachine(state_file=str(state_file))
            mid = _add_model(sm, "persist-b")
            sm._flush_state()
            reloaded = StateMachine(state_file=str(state_file))
            self.assertIsNone(reloaded.get_model(mid).last_started_at)


class TestStartModelHttpPath(unittest.IsolatedAsyncioTestCase):
    async def test_start_model_records_last_started_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            sm = _make_sm(tmp)
            mid = _add_model(sm)
            executor = MagicMock()
            executor._get_activate_cmd.return_value = ""
            server_cfg = ServerConfig(host="10.131.1.7")
            with patch.object(model_service, "state_machine", sm), \
                 patch.object(model_service, "check_ssh_connectivity",
                              return_value={"connected": True}), \
                 patch.object(model_service.launch_config_manager, "load_config",
                              return_value=dict(LAUNCH_CFG)), \
                 patch.object(model_service, "get_remote_executor",
                              return_value=executor), \
                 patch.object(model_service.config_manager, "get_config",
                              return_value=server_cfg), \
                 patch.object(model_service, "resolve_api_target",
                              side_effect=_resolve_target), \
                 patch.object(model_service, "_verify_start_probe",
                              new=AsyncMock()):
                result = await model_service.start_model(mid)
                await asyncio.sleep(0)
            self.assertEqual(result["status"], "loading")
            self.assertIsNotNone(sm.get_model(mid).last_started_at)
            self.assertEqual(sm.get_model(mid).status, ModelStatus.LOADING)


if __name__ == "__main__":
    unittest.main()
