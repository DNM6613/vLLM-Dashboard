import unittest
from unittest.mock import MagicMock, patch

import backend.executor.remote.power_ops as power_ops_mod
from backend.config.remote_client import config_manager
from backend.config.server_config import ServerConfig
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


class TestPowerOffServer(unittest.TestCase):
    """The power-off dialog's four actions (off/soft/reset/cycle) are single
    BMC chassis commands: one ipmitool call, no verification loop."""

    def _run(self, action, proc=None, bmc_host="10.99.99.99"):
        ops = PowerOps()
        config = _FakeConfig(bmc_host=bmc_host)
        with patch.object(power_ops_mod.subprocess, "run") as run_mock, \
                patch.object(power_ops_mod.shutil, "which",
                             return_value="/usr/bin/ipmitool"), \
                patch("backend.config.remote_client.config_manager.get_config",
                      return_value=config):
            if proc is not None:
                run_mock.return_value = proc
            result = ops.power_off_server(action)
        return result, run_mock

    class _Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    def test_each_action_issues_the_matching_chassis_command(self):
        for action in ("off", "soft", "reset", "cycle"):
            result, run_mock = self._run(action, self._Ok())
            self.assertTrue(result["success"], action)
            self.assertEqual(result["action"], action)
            self.assertEqual(run_mock.call_count, 1)
            cmd = run_mock.call_args[0][0]
            self.assertEqual(cmd[-3:], ["chassis", "power", action])

    def test_invalid_action_rejected_without_ipmitool(self):
        result, run_mock = self._run("reboot")
        self.assertFalse(result["success"])
        self.assertIn("Invalid power action", result["error"])
        run_mock.assert_not_called()

    def test_auth_failure_reports_bmc_credentials(self):
        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "Unable to establish IPMI session (code 0x20)\n"

        result, _run_mock = self._run("off", _Proc())
        self.assertFalse(result["success"])
        self.assertIn("authentication failed", result["error"])

    def test_missing_bmc_host(self):
        result, run_mock = self._run("off", bmc_host="")
        self.assertFalse(result["success"])
        self.assertIn("BMC host not configured", result["error"])
        run_mock.assert_not_called()


class TestSshPoweroffServer(unittest.TestCase):
    """The no-BMC fallback: a graceful ``sudo poweroff`` over SSH. Reuses the
    sudo probe/password dance (passwordless first, then the SSH password via
    stdin). ``poweroff`` drops the channel mid-execution, so anything other
    than a channel-open failure means the command was delivered."""

    def _run(self, scripted, password="pw", host="10.99.99.7"):
        from backend.executor.remote_executor import RemoteExecutor

        ex = RemoteExecutor(host=host, username="u", password=password)
        calls = []
        it = iter(scripted)

        def fake_execute(command, timeout=None, stdin_data=None):
            calls.append({"command": command, "stdin_data": stdin_data})
            return next(it)

        ex.execute = fake_execute
        result = ex.ssh_poweroff_server()
        return result, calls

    def test_passwordless_sudo_poweroff(self):
        scripted = [
            {"success": True, "stderr": ""},                    # -n probe ok
            {"success": False, "returncode": -1, "stderr": ""},  # poweroff
        ]
        result, calls = self._run(scripted)
        self.assertTrue(result["success"])  # channel drop = delivered
        self.assertIsNone(result["error"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["command"], "sudo -n -p '' true")
        self.assertIsNone(calls[0]["stdin_data"])
        self.assertEqual(calls[1]["command"], "sudo -n -p '' poweroff")
        self.assertIsNone(calls[1]["stdin_data"])

    def test_password_sudo_feeds_password_on_stdin(self):
        scripted = [
            {"success": False, "stderr": ""},                    # -n probe: needs pw
            {"success": True, "stderr": ""},                     # -S probe ok
            {"success": False, "returncode": -1, "stderr": ""},  # poweroff
        ]
        result, calls = self._run(scripted, password="s3cret")
        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["command"], "sudo -n -p '' true")
        self.assertEqual(calls[1]["command"], "sudo -S -p '' true")
        self.assertEqual(calls[1]["stdin_data"], "s3cret\n")
        self.assertEqual(calls[2]["command"], "sudo -S -p '' poweroff")
        self.assertEqual(calls[2]["stdin_data"], "s3cret\n")

    def test_channel_open_failure_is_not_delivered(self):
        scripted = [
            {"success": True, "stderr": ""},
            {"success": False, "channel_open_failed": True,
             "stderr": "SSH channel open failed: no route"},
        ]
        result, _ = self._run(scripted)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "SSH channel open failed: no route")

    def test_sudo_auth_failure(self):
        scripted = [
            {"success": False, "stderr": ""},   # -n probe: needs pw
            {"success": False, "stderr": "sudo: no tty present"},  # -S probe
        ]
        result, _ = self._run(scripted, password="pw")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "sudo: no tty present")

    def test_no_password_configured(self):
        scripted = [{"success": False, "stderr": ""}]
        result, calls = self._run(scripted, password="")
        self.assertFalse(result["success"])
        self.assertIn("sudo requires a password", result["error"])
        self.assertEqual(len(calls), 1)  # never attempted the -S path

    def test_localhost_rejected(self):
        result, _ = self._run([{"success": True, "stderr": ""}],
                              host="localhost")
        self.assertFalse(result["success"])
        self.assertIn("remote SSH mode", result["error"])


class TestShutdownEndpointDispatch(unittest.TestCase):
    """POST /config/server/shutdown dispatches by channel: with a BMC the
    action is an IPMI chassis command; without one only the SSH fallbacks
    (soft/reboot) apply and the IPMI-only verbs are rejected."""

    def setUp(self):
        self._saved = config_manager.config

    def tearDown(self):
        config_manager.config = self._saved

    def _post(self, action, bmc_host, overrides=None):
        from fastapi.testclient import TestClient

        from backend.main import app
        config_manager.config = ServerConfig(bmc_host=bmc_host)
        ex = MagicMock()
        ex.power_off_server.return_value = {"success": True, "error": None}
        ex.ssh_poweroff_server.return_value = {"success": True, "error": None}
        ex.reboot_server.return_value = {"success": True, "error": None}
        if overrides:
            overrides(ex)
        client = TestClient(app)
        with patch("backend.api.server_config.get_remote_executor",
                   return_value=ex), \
             patch("backend.api.server_config.state_machine") as sm:
            sm.get_all_models.return_value = []
            resp = client.post("/api/v1/config/server/shutdown",
                               json={"action": action})
        return resp, ex

    def test_bmc_present_uses_ipmi(self):
        resp, ex = self._post("off", "10.99.99.6")
        self.assertEqual(resp.status_code, 200)
        ex.power_off_server.assert_called_once_with("off")
        ex.ssh_poweroff_server.assert_not_called()
        ex.reboot_server.assert_not_called()

    def test_bmc_present_rejects_reboot(self):
        resp, ex = self._post("reboot", "10.99.99.6")
        self.assertEqual(resp.status_code, 400)
        ex.power_off_server.assert_not_called()
        ex.reboot_server.assert_not_called()

    def test_no_bmc_soft_uses_ssh_poweroff(self):
        resp, ex = self._post("soft", "")
        self.assertEqual(resp.status_code, 200)
        ex.ssh_poweroff_server.assert_called_once()
        ex.power_off_server.assert_not_called()

    def test_no_bmc_reboot_uses_ssh_reboot(self):
        resp, ex = self._post("reboot", "")
        self.assertEqual(resp.status_code, 200)
        ex.reboot_server.assert_called_once()
        ex.power_off_server.assert_not_called()

    def test_no_bmc_ipmi_verbs_rejected(self):
        for action in ("off", "reset", "cycle"):
            resp, ex = self._post(action, "")
            self.assertEqual(resp.status_code, 400, action)
            ex.power_off_server.assert_not_called()

    def test_ssh_poweroff_failure_is_500(self):
        def boom(ex):
            ex.ssh_poweroff_server.return_value = \
                {"success": False, "error": "boom"}
        resp, _ = self._post("soft", "", overrides=boom)
        self.assertEqual(resp.status_code, 500)
        # Custom error envelope (backend.middleware._error_response).
        self.assertIn("boom", resp.json()["error"]["message"])


if __name__ == "__main__":
    unittest.main()
