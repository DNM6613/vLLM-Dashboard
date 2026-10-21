"""Wall-clock bound on the SSH read phase (audit 2026-10-20 P1-3/P1-4).

The executor's per-command timeout only bounded the channel open; a
half-dead remote (sshd up but silent) could pin ``stdout.read()`` — and the
watchdog / deployment worker threads that own it — forever.
``_read_and_wait_bounded`` runs the read in a worker thread and raises
TimeoutError at the deadline; the abandoned reader must wake (and exit)
when the caller closes the channel.
"""
import threading
import time
import unittest
from unittest import mock

from backend.executor import remote_executor as rex


class _FakeChannel:
    def __init__(self):
        self._wake = threading.Event()

    def recv_exit_status(self):
        return 0

    def close(self):
        self._wake.set()


class _FakeStream:
    def __init__(self, data: bytes, channel: _FakeChannel, block: bool = False):
        self._data = data
        self._channel = channel
        self._block = block
        self.channel = channel

    def read(self):
        if self._block:
            # Simulate a half-dead remote: the read blocks until the caller
            # closes the channel (waking the abandoned reader thread).
            self._channel._wake.wait(10.0)
            raise OSError("channel closed while stalled")
        return self._data


class TestReadAndWaitBounded(unittest.TestCase):
    def test_fast_read_returns_output_and_exit_code(self):
        ch = _FakeChannel()
        out = _FakeStream(b"hello\n", ch)
        err = _FakeStream(b"warn", ch)
        self.assertEqual(
            rex._read_and_wait_bounded(out, err, time.monotonic() + 5),
            (b"hello\n", b"warn", 0))

    def test_stalled_read_raises_timeout_at_deadline(self):
        ch = _FakeChannel()
        out = _FakeStream(b"", ch, block=True)
        err = _FakeStream(b"", ch)
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            rex._read_and_wait_bounded(out, err, time.monotonic() + 0.2)
        # The caller is released at the deadline, not after the 10 s stall.
        self.assertLess(time.monotonic() - t0, 2.0)
        ch.close()  # let the abandoned reader below exit

    def test_abandoned_reader_wakes_on_channel_close(self):
        ch = _FakeChannel()
        out = _FakeStream(b"", ch, block=True)
        err = _FakeStream(b"", ch)
        threads: list[threading.Thread] = []
        orig = threading.Thread

        def _spy(*a, **k):
            t = orig(*a, **k)
            threads.append(t)
            return t

        with mock.patch.object(rex.threading, "Thread", _spy), self.assertRaises(TimeoutError):
            rex._read_and_wait_bounded(out, err, time.monotonic() + 0.2)
        self.assertTrue(threads)
        ch.close()
        deadline = time.monotonic() + 3.0
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        self.assertTrue(all(not t.is_alive() for t in threads))
