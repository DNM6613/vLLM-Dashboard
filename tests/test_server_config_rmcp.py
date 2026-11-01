"""bmc_rmcp_port (v6 BMC RMCP port): model default + 1–65535 validation."""
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.api.server_config import ServerConfigUpdate
from backend.config.remote_client import config_manager
from backend.config.server_config import ServerConfig


class TestBmcRmcpPort(unittest.TestCase):
    def setUp(self):
        self._saved = config_manager.config
        config_manager.config = ServerConfig()

    def tearDown(self):
        config_manager.config = self._saved

    def test_default_623(self):
        self.assertEqual(ServerConfig().bmc_rmcp_port, 623)

    def test_bounds(self):
        self.assertEqual(ServerConfig(bmc_rmcp_port=1).bmc_rmcp_port, 1)
        self.assertEqual(ServerConfig(bmc_rmcp_port=65535).bmc_rmcp_port, 65535)
        with self.assertRaises(ValidationError):
            ServerConfig(bmc_rmcp_port=0)
        with self.assertRaises(ValidationError):
            ServerConfig(bmc_rmcp_port=65536)

    def test_merge_validates_and_persists(self):
        # _save_config is stubbed so the test never touches data/ on disk.
        with patch.object(config_manager, "_save_config"):
            merged = config_manager.merge_config({"bmc_rmcp_port": 9999})
            self.assertEqual(merged.bmc_rmcp_port, 9999)
            self.assertEqual(config_manager.get_config().bmc_rmcp_port, 9999)
            with self.assertRaises(ValidationError):
                config_manager.merge_config({"bmc_rmcp_port": 99999})

    def test_update_model_accepts_field(self):
        upd = ServerConfigUpdate(bmc_rmcp_port=9999)
        self.assertEqual(upd.model_dump(exclude_unset=True)["bmc_rmcp_port"], 9999)


if __name__ == "__main__":
    unittest.main()
