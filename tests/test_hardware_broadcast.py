"""Audit 2026-10-20 P2-8: the hardware broadcaster sends concurrently —
N slow/half-open subscribers must not serially stretch the 0.5 s poll
cycle to 3N seconds (every subscriber's data lagging by that much)."""
import asyncio
import unittest

from backend.ws.hardware_broadcaster import HardwareBroadcaster


class _FakeWS:
    def __init__(self, delay: float = 0.0, fail: bool = False):
        self.delay = delay
        self.fail = fail
        self.received: list[str] = []

    async def send_text(self, msg: str):
        if self.fail:
            raise RuntimeError("send failed")
        if self.delay:
            await asyncio.sleep(self.delay)
        self.received.append(msg)


class TestBroadcast(unittest.TestCase):
    def test_slow_clients_do_not_block_each_other(self):
        b = HardwareBroadcaster()
        b._BROADCAST_SEND_TIMEOUT = 0.3
        a, c = _FakeWS(delay=0.2), _FakeWS(delay=0.2)
        b._clients.add(a)
        b._clients.add(c)
        asyncio.run(b._broadcast({"gpus": []}))
        # Serial sends would need 0.4 s > the 0.3 s per-client bound: the
        # second send would time out and the client be dropped. Concurrent
        # sends land for both within the bound.
        self.assertEqual(len(a.received), 1)
        self.assertEqual(len(c.received), 1)
        self.assertEqual(b._clients, {a, c})

    def test_failing_client_is_dropped_others_kept(self):
        b = HardwareBroadcaster()
        bad = _FakeWS(fail=True)
        good = _FakeWS()
        b._clients.add(bad)
        b._clients.add(good)
        asyncio.run(b._broadcast({"gpus": []}))
        self.assertEqual(len(good.received), 1)
        self.assertEqual(b._clients, {good})

    def test_no_clients_no_send(self):
        b = HardwareBroadcaster()
        self.assertEqual(asyncio.run(b._broadcast({"gpus": []})), None)
