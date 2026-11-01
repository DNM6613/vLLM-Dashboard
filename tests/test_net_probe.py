"""v6 header lights: ICMP echo probe + 3-strike PingTracker.

No test touches the real network: the socket is faked at the
``socket.socket`` boundary, so these run on Windows too (where
unprivileged DGRAM ICMP sockets need admin rights).
"""
import struct
import unittest
from unittest.mock import patch

from backend.executor.remote import net_probe


class TestPingTracker(unittest.TestCase):
    def setUp(self):
        self.tr = net_probe.PingTracker()

    def test_up_on_success(self):
        state, rtt = self.tr.record("h", 0.005)
        self.assertEqual(state, "up")
        self.assertAlmostEqual(rtt, 0.005)

    def test_degraded_then_down_then_reset(self):
        self.assertEqual(self.tr.record("h", None)[0], "degraded")
        self.assertEqual(self.tr.record("h", None)[0], "degraded")
        self.assertEqual(self.tr.record("h", None)[0], "down")
        self.assertEqual(self.tr.record("h", None)[0], "down")  # stays down
        # A single success resets to up immediately (the light clears).
        state, rtt = self.tr.record("h", 0.001)
        self.assertEqual(state, "up")
        self.assertEqual(rtt, 0.001)

    def test_hosts_independent(self):
        self.assertEqual(self.tr.record("a", None)[0], "degraded")
        self.assertEqual(self.tr.record("b", 0.001)[0], "up")
        self.assertEqual(self.tr.record("a", None)[0], "degraded")

    def test_reset(self):
        self.tr.record("h", None)
        self.tr.record("h", None)
        self.tr.reset("h")
        self.assertEqual(self.tr.record("h", None)[0], "degraded")


class _FakeIcmpSocket:
    """Records the sent echo request and answers it as a type-0 reply."""

    def __init__(self, fail: bool = False):
        self.sent = None
        self.fail = fail

    def settimeout(self, t):
        pass

    def sendto(self, data, addr):
        self.sent = data

    def recv(self, n):
        # A DGRAM socket's recv() returns the raw datagram bytes (the
        # address is only delivered by recvfrom()) — verified live in the
        # production container. The kernel also rewrites the reply ident
        # to its per-socket value (0x8fab here), so the reply must be
        # matched on seq alone, never on the sent ident.
        if self.fail:
            raise TimeoutError("no reply")
        _rtype, _code, _cks, _ident, seq = struct.unpack("!BBHHH", self.sent[:8])
        return struct.pack("!BBHHH", 0, 0, 0, 0x8FAB, seq) + self.sent[8:]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestIcmpPing(unittest.TestCase):
    def test_success_returns_rtt(self):
        fake = _FakeIcmpSocket()
        with patch.object(net_probe.socket, "socket", return_value=fake):
            rtt = net_probe.icmp_ping("10.99.99.99", timeout=1.0)
        self.assertIsNotNone(rtt)
        self.assertGreaterEqual(rtt, 0.0)
        self.assertLess(rtt, 1.0)
        # The sent packet is a type-8 (echo request) ICMP header.
        self.assertEqual(struct.unpack("!B", fake.sent[:1])[0], 8)

    def test_timeout_returns_none(self):
        with patch.object(net_probe.socket, "socket",
                          return_value=_FakeIcmpSocket(fail=True)):
            self.assertIsNone(net_probe.icmp_ping("10.99.99.99"))

    def test_socket_error_returns_none(self):
        # Windows / unprivileged environments: the constructor itself fails.
        with patch.object(net_probe.socket, "socket",
                          side_effect=PermissionError("no unprivileged icmp")):
            self.assertIsNone(net_probe.icmp_ping("10.99.99.99"))


if __name__ == "__main__":
    unittest.main()
