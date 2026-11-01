"""v6 SSH light: ping-first probing, fresh per-request ping, net_state merge.

No real ping / SSH happens — icmp_ping and the shared pool are patched.
"""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.config import remote_client
from backend.executor.remote import ssh_probe as probe

HOST = "10.99.99.99"
CFG = SimpleNamespace(host=HOST, ssh_port=22, ssh_username="u",
                      ssh_password="p", ssh_key_path="")


class _Base(unittest.TestCase):
    def setUp(self):
        self._fp = probe._ssh_probe_fp
        self._cache = probe._ssh_probe_cache
        self._ts = probe._ssh_probe_ts
        probe.ssh_ping.reset(HOST)

    def tearDown(self):
        probe._ssh_probe_fp = self._fp
        probe._ssh_probe_cache = self._cache
        probe._ssh_probe_ts = self._ts
        probe.ssh_ping.reset(HOST)


class _FakeStdout:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, n=None):
        return self._data


class TestProbeOncePingFirst(_Base):
    def test_ping_down_skips_pool(self):
        with patch.object(probe, "icmp_ping", return_value=None), \
             patch.object(probe.ssh_pool, "get") as pool_get:
            out = probe._probe_once(CFG)
        self.assertFalse(out["connected"])
        self.assertEqual(out["error_code"], "ping")
        self.assertTrue(out["host_down"])
        self.assertEqual(out["net_state"], "down")
        pool_get.assert_not_called()

    def test_ping_ok_echo_ok_connected(self):
        client = SimpleNamespace(
            exec_command=lambda cmd, timeout=None: (
                None, _FakeStdout(b"ok\n"), None))
        with patch.object(probe, "icmp_ping", return_value=0.002), \
             patch.object(probe.ssh_pool, "get", return_value=client), \
             patch.object(probe.ssh_pool, "release"):
            out = probe._probe_once(CFG)
        self.assertTrue(out["connected"])
        self.assertIsNone(out["error_code"])
        self.assertEqual(out["net_state"], "up")

    def test_auth_error_classified_auth(self):
        class _AuthErr(Exception):
            def __str__(self):
                return "Authentication failed for user u"

        with patch.object(probe, "icmp_ping", return_value=0.002), \
             patch.object(probe.ssh_pool, "get", side_effect=_AuthErr()):
            out = probe._probe_once(CFG)
        self.assertFalse(out["connected"])
        self.assertEqual(out["error_code"], "auth")
        self.assertEqual(out["net_state"], "up")

    def test_refused_classified_no_response(self):
        with patch.object(probe, "icmp_ping", return_value=0.002), \
             patch.object(probe.ssh_pool, "get",
                          side_effect=ConnectionRefusedError("refused")):
            out = probe._probe_once(CFG)
        self.assertFalse(out["connected"])
        self.assertEqual(out["error_code"], "no_response")
        self.assertEqual(out["net_state"], "up")

    def test_unconfigured_host(self):
        cfg = SimpleNamespace(host="", ssh_port=22, ssh_username="",
                              ssh_password=None, ssh_key_path=None)
        out = probe._probe_once(cfg)
        self.assertFalse(out["connected"])
        self.assertEqual(out["error_code"], "not_configured")


class TestCheckSshConnectivityNetMerge(_Base):
    def _prime_cache(self, connected=True, age=0.1):
        probe._ssh_probe_cache = {"connected": connected, "error": None,
                                  "latency_ms": 1}
        probe._ssh_probe_fp = (HOST, 22, "u", "p", "")
        probe._ssh_probe_ts = time.monotonic() - age

    def test_first_ping_failure_degraded_keeps_service(self):
        self._prime_cache(connected=True)
        with patch.object(probe, "icmp_ping", return_value=None), \
             patch.object(remote_client.config_manager, "get_config",
                          return_value=CFG):
            out = probe.check_ssh_connectivity()
        self.assertEqual(out["net_state"], "degraded")
        self.assertTrue(out["connected"])  # service layer: last known
        self.assertTrue(out["host_down"])  # degraded counts as not-verified

    def test_third_ping_failure_down_forces_disconnected(self):
        self._prime_cache(connected=True)
        with patch.object(probe, "icmp_ping", return_value=None), \
             patch.object(remote_client.config_manager, "get_config",
                          return_value=CFG):
            for _ in range(3):
                out = probe.check_ssh_connectivity()
        self.assertEqual(out["net_state"], "down")
        self.assertFalse(out["connected"])
        self.assertEqual(out["error_code"], "ping")

    def test_ping_success_resets_streak(self):
        self._prime_cache(connected=True)
        with patch.object(probe, "icmp_ping", side_effect=[None, 0.001]), \
             patch.object(remote_client.config_manager, "get_config",
                          return_value=CFG):
            out1 = probe.check_ssh_connectivity()
            out2 = probe.check_ssh_connectivity()
        self.assertEqual(out1["net_state"], "degraded")
        self.assertEqual(out2["net_state"], "up")
        self.assertIn("ping_rtt_ms", out2)

    def test_unconfigured_early_return(self):
        cfg = SimpleNamespace(host="", ssh_port=22, ssh_username="",
                              ssh_password=None, ssh_key_path=None)
        with patch.object(remote_client.config_manager, "get_config",
                          return_value=cfg):
            out = probe.check_ssh_connectivity()
        self.assertEqual(out["error_code"], "not_configured")
        self.assertEqual(out["net_state"], "down")


if __name__ == "__main__":
    unittest.main()
