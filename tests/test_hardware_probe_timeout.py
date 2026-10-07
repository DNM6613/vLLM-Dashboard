"""Hardware endpoints / broadcaster must bound stalled remote SSH reads.

Regression coverage for audit H2: the executor's per-command timeout only
covers opening the SSH channel, so a stalled sshd used to hang the hardware
polling endpoints indefinitely (leaking a default-pool worker thread per
request). The endpoints now answer 504 and the WS poll loop falls back to
the degraded payload instead of being pinned.
"""
import asyncio
import json
import time
import unittest
from unittest import mock

from fastapi import HTTPException

from backend.api import hardware as hw
from backend.config import remote_client
from backend.config.server_config import ServerConfig
from backend.config.settings import settings


class StalledExecutor:
    """Remote executor whose reads exceed the probe wall-clock bound."""

    def get_system_info_combined(self):
        time.sleep(0.5)
        return {"cpu": {}, "memory": {}, "disk": {}}

    def get_software_info(self):
        time.sleep(0.5)
        return {"os": "Ubuntu", "gpu_driver": "580", "cuda_toolkit": "", "vllm": ""}

    def get_nvidia_smi(self):
        time.sleep(0.5)
        return {"gpus": []}


def _remote_config():
    return ServerConfig(id="default", host="10.0.0.5")


class TestHardwareProbeTimeout(unittest.TestCase):
    def _patched_remote(self, executor=StalledExecutor()):
        return (
            mock.patch.object(
                remote_client.config_manager, "get_config",
                return_value=_remote_config()),
            mock.patch.object(
                hw, "get_remote_executor", return_value=executor),
        )

    def test_metrics_returns_504_when_remote_read_stalls(self):
        cfg_patch, exec_patch = self._patched_remote()
        with cfg_patch, exec_patch, \
             mock.patch.object(
                 hw, "get_gpu_info_remote",
                 side_effect=lambda: time.sleep(0.5) or []), \
             mock.patch.object(settings, "HARDWARE_PROBE_TIMEOUT", 0.1), \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(hw.get_hardware_metrics())
        self.assertEqual(ctx.exception.status_code, 504)

    def test_software_returns_504_when_remote_read_stalls(self):
        cfg_patch, exec_patch = self._patched_remote()
        with cfg_patch, exec_patch, \
             mock.patch.object(settings, "HARDWARE_PROBE_TIMEOUT", 0.1), \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(hw.get_software_info())
        self.assertEqual(ctx.exception.status_code, 504)

    def test_metrics_still_succeeds_when_remote_is_fast(self):
        cfg_patch, _ = self._patched_remote()
        with cfg_patch, \
             mock.patch.object(
                 hw, "get_gpu_info_remote",
                 return_value=[{"index": 0}]), \
             mock.patch.object(settings, "HARDWARE_PROBE_TIMEOUT", 5.0):
            result = asyncio.run(hw.get_hardware_metrics())
        self.assertIn("gpus", result)
        self.assertIn("cpu", result)
        self.assertIn("timestamp", result)


class FakeWs:
    def __init__(self):
        self.messages = []
        self.first = asyncio.Event()

    async def send_text(self, message):
        self.messages.append(message)
        self.first.set()


class TestBroadcasterSurvivesStalledSnapshot(unittest.TestCase):
    def test_poll_loop_keeps_running_after_stalled_snapshot(self):
        import backend.ws.hardware_broadcaster as hb

        def stall():
            time.sleep(0.5)

        async def scenario():
            bc = hb.HardwareBroadcaster()
            ws = FakeWs()
            bc.register(ws)
            await ws.first.wait()
            alive = bc._poll_task is not None and not bc._poll_task.done()
            bc.unregister(ws)
            await bc.shutdown()
            return ws.messages, alive

        with mock.patch.object(settings, "HARDWARE_PROBE_TIMEOUT", 0.1), \
             mock.patch.object(hb, "_get_hardware_snapshot", stall):
            messages, alive = asyncio.run(scenario())

        self.assertTrue(alive, "poll loop died after a stalled snapshot")
        self.assertEqual(len(messages), 1)
        payload = json.loads(messages[0])
        self.assertEqual(payload["type"], "gpu_metrics")
        self.assertIn("error", payload["data"])
        self.assertEqual(payload["data"]["gpus"], [])


if __name__ == "__main__":
    unittest.main()
