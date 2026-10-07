import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.config.remote_client import ApiTarget
from backend.config.server_config import ServerConfig
from backend.schemas.model import ModelStatus
from backend.service import model_service

LAUNCH_CFG = {"start_command": "vllm serve m1", "env_vars": ""}


def _resolve_target_guard(config):
    # Regression guard: the launch config (a plain dict) must never be passed
    # here as the server config — that crashed on config.extra_headers
    # ('dict' object has no attribute 'extra_headers').
    assert isinstance(config, ServerConfig), f"expected ServerConfig, got {type(config).__name__}"
    return ApiTarget(
        base_url=f"http://{config.host}:8000",
        port=8000,
        headers={},
        api_key=None,
    )


class TestStartModelProbe(unittest.IsolatedAsyncioTestCase):
    async def test_probe_task_uses_server_config(self):
        fake_model = SimpleNamespace(status=ModelStatus.STOPPED)
        executor = MagicMock()
        executor._get_activate_cmd.return_value = ""
        server_cfg = ServerConfig(host="10.131.1.7")

        with patch.object(model_service.state_machine, "get_model", return_value=fake_model), \
             patch.object(model_service.state_machine, "update_model_status", return_value=True), \
             patch.object(model_service.state_machine, "set_current_model", return_value=True), \
             patch.object(model_service, "check_ssh_connectivity",
                          return_value={"connected": True}), \
             patch.object(model_service.launch_config_manager, "load_config",
                          return_value=dict(LAUNCH_CFG)), \
             patch.object(model_service, "get_remote_executor", return_value=executor), \
             patch.object(model_service.config_manager, "get_config", return_value=server_cfg), \
             patch.object(model_service, "resolve_api_target",
                          side_effect=_resolve_target_guard), \
             patch.object(model_service, "_verify_start_probe", new=AsyncMock()) as probe:
            result = await model_service.start_model("m1")
            await asyncio.sleep(0)

        self.assertEqual(result["status"], "loading")
        self.assertEqual(result["command"], "vllm serve m1")
        probe.assert_awaited_once_with("m1", "10.131.1.7", 8000)

    async def test_start_rejects_already_loading(self):
        fake_model = SimpleNamespace(status=ModelStatus.LOADING)
        with patch.object(model_service.state_machine, "get_model", return_value=fake_model), \
             self.assertRaises(Exception) as ctx:
            await model_service.start_model("m1")
        self.assertIn("already loading", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
