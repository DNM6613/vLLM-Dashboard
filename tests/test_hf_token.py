"""HF token support: config validation, API masking, download command injection."""
import unittest
from functools import partial
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from backend.api.server_config import _mask_credentials
from backend.config.remote_client import config_manager
from backend.config.server_config import CREDENTIAL_MASK, ServerConfig
from backend.executor.remote import model_ops
from backend.executor.remote_executor import RemoteExecutor


def _make_ops() -> model_ops.ModelOps:
    # Bare mixin instance: stub the two helpers normally provided by
    # RemoteExecutor (activate venv + double-quoted escape) faithfully.
    ops = model_ops.ModelOps()
    ops.execute = MagicMock(return_value={"success": True, "stdout": "4242", "stderr": ""})
    ops._get_activate_cmd = lambda: ""
    ops._DQ_ESCAPE_RE = RemoteExecutor._DQ_ESCAPE_RE
    ops._escape_double_quoted = partial(RemoteExecutor._escape_double_quoted, ops)
    return ops


class TestHfTokenValidation(unittest.TestCase):
    def test_default_is_none(self):
        self.assertIsNone(ServerConfig().hf_token)

    def test_accepted_and_stripped(self):
        cfg = ServerConfig(hf_token="  hf_abc123  ")
        self.assertEqual(cfg.hf_token, "hf_abc123")

    def test_blank_becomes_none(self):
        self.assertIsNone(ServerConfig(hf_token="   ").hf_token)

    def test_non_string_rejected(self):
        with self.assertRaises(ValidationError):
            ServerConfig(hf_token=123)

    def test_too_long_rejected(self):
        with self.assertRaises(ValidationError):
            ServerConfig(hf_token="x" * 257)


class TestHfTokenMasking(unittest.TestCase):
    def test_masked_on_api_response(self):
        cfg = ServerConfig(hf_token="hf_secret")
        masked = _mask_credentials(cfg)
        self.assertEqual(masked.hf_token, CREDENTIAL_MASK)
        self.assertEqual(cfg.hf_token, "hf_secret")

    def test_unset_stays_none(self):
        masked = _mask_credentials(ServerConfig())
        self.assertIsNone(masked.hf_token)

    def test_encrypted_field_list_includes_hf_token(self):
        # hf_token must be encrypted at rest like the other credentials.
        from backend.config import remote_client

        self.assertIn("hf_token", remote_client._CRED_FIELDS)


class TestHfTokenDownloadCommand(unittest.TestCase):
    def test_token_injected_into_download_command(self):
        ops = _make_ops()
        with patch.object(config_manager, "get_config",
                          return_value=ServerConfig(hf_token="hf_secret_token")):
            ops.download_model("org/name", hf_mirror=False)
        cmd = ops.execute.call_args[0][0]
        self.assertIn("export HF_TOKEN=hf_secret_token", cmd)
        self.assertIn("hf download org/name", cmd)

    def test_no_token_no_hf_token_export(self):
        ops = _make_ops()
        with patch.object(config_manager, "get_config",
                          return_value=ServerConfig()):
            ops.download_model("org/name", hf_mirror=False)
        cmd = ops.execute.call_args[0][0]
        self.assertNotIn("HF_TOKEN", cmd)

    def test_total_size_api_gets_bearer_auth(self):
        ops = _make_ops()
        ops.execute.return_value = {"success": True, "stdout": "123456", "stderr": ""}
        with patch.object(config_manager, "get_config",
                          return_value=ServerConfig(hf_token="hf_abc")):
            result = ops.get_download_total_size("org/name")
        cmd = ops.execute.call_args[0][0]
        self.assertIn("Authorization: Bearer hf_abc", cmd)
        self.assertEqual(result["total_bytes"], 123456)

    def test_total_size_anonymous_without_token(self):
        ops = _make_ops()
        ops.execute.return_value = {"success": True, "stdout": "99", "stderr": ""}
        with patch.object(config_manager, "get_config",
                          return_value=ServerConfig()):
            ops.get_download_total_size("org/name")
        cmd = ops.execute.call_args[0][0]
        self.assertNotIn("Authorization", cmd)


if __name__ == "__main__":
    unittest.main()
