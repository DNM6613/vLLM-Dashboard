import unittest
from unittest.mock import patch

import backend.executor.remote.power_ops as power_ops_mod
from backend.executor.remote import ssh_probe as ssh_probe_mod
from backend.executor.remote.power_ops import PowerOps


class _Clock:
    """Deterministic monotonic clock: sleep() advances time instead of blocking."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestPowerOnServer(unittest.TestCase):
    def _config(self):
        config = _FakeConfig()
        return config

    def _run(self, power_on, status, ssh_probe_result, total_timeout=120):
        clock = _Clock()
        ops = PowerOps()
        ops.POWERON_TOTAL_TIMEOUT = total_timeout
        # Shadow the real helpers so no subprocess / socket / SSH is touched.
        ops._ipmi_power_on = power_on
        ops.check_bmc_status = status
        probe_calls = {"n": 0}

        def ssh_probe(refresh=True):
            probe_calls["n"] += 1
            return ssh_probe_result

        with patch.object(power_ops_mod.shutil, "which", return_value="/usr/bin/ipmitool"), \
                patch.object(power_ops_mod.time, "monotonic", clock.monotonic), \
                patch.object(power_ops_mod.time, "sleep", clock.sleep), \
                patch.object(ssh_probe_mod, "check_ssh_connectivity", side_effect=ssh_probe), \
                patch("backend.config.remote_client.config_manager.get_config",
                      return_value=self._config()):
            result = ops.power_on_server()
        return result, probe_calls["n"]

    def test_succeeds_when_bmc_confirms_on(self):
        calls = {"ipo": 0}

        def power_on(config, path):
            calls["ipo"] += 1
            return (True, None)

        def status():
            return {"power": "on", "connected": True, "configured": True}

        result, probes = self._run(power_on, status, {"connected": False, "host_down": True})
        self.assertTrue(result["success"])
        self.assertEqual(result.get("power"), "on")
        self.assertEqual(calls["ipo"], 1)
        self.assertEqual(probes, 0)  # BMC confirmed first; SSH never needed

    def test_retries_when_first_power_on_is_dropped(self):
        # First IPMI command is dropped (rc!=0); power only reaches "on" after
        # the second (idempotent) attempt. This is the real-world "click twice"
        # case the fix targets.
        state = {"ipo": 0}

        def power_on(config, path):
            state["ipo"] += 1
            if state["ipo"] == 1:
                return (False, "BMC busy")
            return (True, None)

        def status():
            power = "on" if state["ipo"] >= 2 else "off"
            return {"power": power, "connected": True, "configured": True}

        result, _probes = self._run(power_on, status, {"connected": False, "host_down": True})
        self.assertTrue(result["success"])
        self.assertEqual(state["ipo"], 2)

    def test_succeeds_via_network_when_bmc_unreachable(self):
        # BMC is dead (the known dev-server case), but the target's own TCP
        # port answers: the chassis is on and the OS is booting — that is a
        # successful power-on even though the BMC never confirms.
        def power_on(config, path):
            return (False, "Cannot reach BMC or BMC authentication failed (check BMC address and credentials)")

        def status():
            return {"power": None, "connected": False, "configured": True}

        result, probes = self._run(
            power_on, status,
            {"connected": False, "error": "Connection refused", "host_down": False})
        self.assertTrue(result["success"])
        self.assertEqual(result.get("power"), "on")
        self.assertIn("network", result.get("note") or "")
        self.assertGreaterEqual(probes, 1)

    def test_succeeds_via_ssh_when_remote_up(self):
        def power_on(config, path):
            return (False, "BMC power-on failed: timeout")

        def status():
            return {"power": "off", "connected": True, "configured": True}

        result, _probes = self._run(power_on, status, {"connected": True, "latency_ms": 12})
        self.assertTrue(result["success"])
        self.assertIn("SSH", result.get("note") or "")

    def test_times_out_when_power_never_reaches_on(self):
        def power_on(config, path):
            return (False, "BMC power-on failed: timeout")

        def status():
            return {"power": "off", "connected": True, "configured": True}

        result, probes = self._run(
            power_on, status,
            {"connected": False, "host_down": True, "error": "host unreachable (TCP probe no response)"},
            total_timeout=20)
        self.assertFalse(result["success"])
        self.assertIn("BMC", result["error"])
        self.assertIn("did not come online", result["error"])
        self.assertGreaterEqual(probes, 1)

    def test_reports_config_errors_without_retrying(self):
        ops = PowerOps()
        config = _FakeConfig(bmc_host="")
        with patch("backend.config.remote_client.config_manager.get_config",
                   return_value=config):
            result = ops.power_on_server()
        self.assertFalse(result["success"])
        self.assertIn("BMC host not configured", result["error"])


class _FakeConfig:
    def __init__(self, bmc_host="10.99.99.99", bmc_rmcp_port=623):
        self.bmc_host = bmc_host
        self.bmc_username = "admin"
        self.bmc_password = "secret"
        self.bmc_rmcp_port = bmc_rmcp_port
        self.host = "10.99.99.7"


class TestCheckBmcStatusNetProbe(unittest.TestCase):
    """v6: the BMC light's network layer is an ICMP ping (2-strike tracker);
    a ping failure skips the ipmitool round trip entirely."""

    def setUp(self):
        self.ops = PowerOps()
        self._saved_diag_ts = power_ops_mod._bmc_diag_last_ts
        power_ops_mod._bmc_diag_last_ts = 0.0
        power_ops_mod.bmc_ping.reset("10.99.99.99")

    def tearDown(self):
        power_ops_mod._bmc_diag_last_ts = self._saved_diag_ts
        power_ops_mod.bmc_ping.reset("10.99.99.99")

    def _patched(self, ping_rtt):
        config = _FakeConfig()
        return (
            patch("backend.config.remote_client.config_manager.get_config",
                  return_value=config),
            patch.object(power_ops_mod.shutil, "which",
                         return_value="/usr/bin/ipmitool"),
            patch.object(power_ops_mod, "icmp_ping", return_value=ping_rtt),
        )

    def test_ping_down_skips_ipmitool(self):
        run = patch.object(power_ops_mod.subprocess, "run")
        p1, p2, p3 = self._patched(None)
        with p1, p2, p3, run as run_mock:
            out = self.ops.check_bmc_status()
        # One failed ping → degraded (yellow), ipmitool never runs.
        self.assertEqual(out["net_state"], "degraded")
        self.assertEqual(out["error_code"], "ping")
        self.assertFalse(out["connected"])
        self.assertIsNone(out["power"])
        run_mock.assert_not_called()
        # Two consecutive failures → down (red).
        with p1, p2, p3:
            for _ in range(2):
                out = self.ops.check_bmc_status()
        self.assertEqual(out["net_state"], "down")
        self.assertEqual(out["error_code"], "ping")
        run_mock.assert_not_called()

    def test_ping_ok_runs_ipmitool_with_rmcp_port(self):
        class _Proc:
            returncode = 0
            stdout = "Chassis Power is on\n"
            stderr = ""

        run = patch.object(power_ops_mod.subprocess, "run")
        p1, p2, p3 = self._patched(0.005)
        with p1, p2, p3, run as run_mock:
            run_mock.return_value = _Proc()
            out = self.ops.check_bmc_status()
        self.assertEqual(out["net_state"], "up")
        self.assertEqual(out["error_code"], None)
        self.assertTrue(out["connected"])
        self.assertEqual(out["power"], "on")
        self.assertIsInstance(out["ping_rtt_ms"], float)
        args, kwargs = run_mock.call_args
        cmd = args[0] if args else kwargs["cmd"]
        self.assertIn("-P", cmd)
        self.assertEqual(cmd[cmd.index("-P") + 1], "623")
        self.assertEqual(kwargs["env"]["IPMI_PASSWORD"], "secret")

    def test_ipmitool_auth_failure_classified_auth(self):
        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "Unable to establish IPMI session (code 0x20)\n"

        run = patch.object(power_ops_mod.subprocess, "run")
        p1, p2, p3 = self._patched(0.005)
        with p1, p2, p3, run as run_mock:
            run_mock.return_value = _Proc()
            out = self.ops.check_bmc_status()
        self.assertEqual(out["net_state"], "up")
        self.assertEqual(out["error_code"], "auth")
        self.assertFalse(out["connected"])

    def test_power_on_cmd_includes_rmcp_port(self):
        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        config = _FakeConfig(bmc_rmcp_port=6666)
        with patch.object(power_ops_mod.subprocess, "run") as run_mock:
            run_mock.return_value = _Proc()
            ok, _err = self.ops._ipmi_power_on(config, "/usr/bin/ipmitool")
        self.assertTrue(ok)
        cmd = run_mock.call_args[0][0]
        self.assertEqual(cmd[cmd.index("-P") + 1], "6666")


if __name__ == "__main__":
    unittest.main()
