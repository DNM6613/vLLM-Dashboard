import unittest
from unittest import mock

from backend.config import remote_client
from backend.config.server_config import ServerConfig


class TestResolveApiTargetPortFallback(unittest.TestCase):
    def _config(self, **overrides):
        base = {"id": "default", "host": "10.0.0.5", "ssh_port": 22}
        base.update(overrides)
        return ServerConfig(**base)

    def test_default_is_vllm_default(self):
        self.assertEqual(remote_client.VLLM_DEFAULT_PORT, 8000)

    def test_falls_back_to_vllm_default_when_launch_has_no_port(self):
        # No --port flag and no PORT env var in the launch config: the API
        # target must use vLLM's default port, not a stored config value.
        with mock.patch.object(
            remote_client, "resolve_launch_api", return_value=(None, None)
        ):
            target = remote_client.resolve_api_target(self._config())
        self.assertEqual(target.port, remote_client.VLLM_DEFAULT_PORT)
        self.assertEqual(
            target.base_url, f"http://10.0.0.5:{remote_client.VLLM_DEFAULT_PORT}"
        )

    def test_launch_port_wins_over_default(self):
        with mock.patch.object(
            remote_client, "resolve_launch_api", return_value=(8123, None)
        ):
            target = remote_client.resolve_api_target(self._config())
        self.assertEqual(target.port, 8123)
        self.assertEqual(target.base_url, "http://10.0.0.5:8123")


class TestServerConfigPortRemoved(unittest.TestCase):
    def test_legacy_saved_port_key_is_ignored(self):
        # Existing server_config.json files carry a "port" key. Loading one
        # must still succeed and simply drop the key instead of failing.
        cfg = ServerConfig(**{"id": "default", "host": "10.0.0.5", "port": 8000})
        self.assertFalse(hasattr(cfg, "port"))

    def test_port_not_serialized(self):
        cfg = ServerConfig(id="default", host="10.0.0.5")
        self.assertNotIn("port", cfg.model_dump())

    def test_update_payload_drops_port(self):
        from backend.api.server_config import ServerConfigUpdate

        upd = ServerConfigUpdate(host="10.0.0.5", port=9000)
        self.assertNotIn("port", upd.model_dump(exclude_unset=True))


if __name__ == "__main__":
    unittest.main()
