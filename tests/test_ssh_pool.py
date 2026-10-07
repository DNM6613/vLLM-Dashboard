"""SSHConnectionPool concurrency semantics.

Regression coverage for the audit H1 fix: the per-key connection lock must
only cover the acquire/create phase. A long in-flight command on one
connection must not block (let alone fail) concurrent commands to the same
host — paramiko multiplexes concurrent channels per transport. Also covers
the release/evict fix: a dead client being released/evicted must never drop
a replacement connection that another thread pooled in the meantime.
"""
import time
import unittest

from backend.executor.remote.ssh import SSHConnectionPool


class FakeTransport:
    def __init__(self):
        self.active = True

    def is_active(self):
        return self.active


class FakeClient:
    def __init__(self):
        self.transport = FakeTransport()
        self.closed = False

    def get_transport(self):
        return self.transport

    def close(self):
        self.closed = True
        self.transport.active = False


def make_pool(create_fn=None, **kwargs):
    pool = SSHConnectionPool(**kwargs)
    # Never touch the network (tests/__init__.py guard): default to a fake
    # client factory unless the test overrides connection creation.
    pool._create_client = create_fn or (lambda *a, **k: FakeClient())
    return pool


class TestSSHConnectionPool(unittest.TestCase):
    HOST, PORT, USER = "10.0.0.1", 22, "root"
    KEY = ("10.0.0.1", 22, "root")

    def test_second_caller_reuses_pooled_client(self):
        pool = make_pool()
        first = pool.get(self.HOST, self.PORT, self.USER)
        second = pool.get(self.HOST, self.PORT, self.USER)
        self.assertIs(second, first)
        pool.release(first, self.HOST, self.PORT, self.USER)
        pool.release(second, self.HOST, self.PORT, self.USER)

    def test_in_flight_command_does_not_block_concurrent_get(self):
        """H1: with an in-flight command holding the client, a concurrent
        get() must return immediately instead of waiting on a lock that is
        held for the whole command duration."""
        pool = make_pool(lock_timeout=1.0)
        first = pool.get(self.HOST, self.PORT, self.USER)
        time.sleep(0.3)  # simulate the in-flight command
        start = time.monotonic()
        second = pool.get(self.HOST, self.PORT, self.USER)
        elapsed = time.monotonic() - start
        self.assertIs(second, first)
        self.assertLess(elapsed, 0.2, "concurrent get() waited on conn_lock")
        pool.release(first, self.HOST, self.PORT, self.USER)
        pool.release(second, self.HOST, self.PORT, self.USER)

    def test_release_dead_client_keeps_replacement(self):
        """Releasing a dead client must not drop a healthy replacement that
        another thread pooled in the meantime (would leak the replacement)."""
        pool = make_pool()
        stale = pool.get(self.HOST, self.PORT, self.USER)
        stale.transport.active = False
        replacement = FakeClient()
        with pool._lock:
            pool._pool[self.KEY] = (replacement, time.time())
            pool._refcounts[self.KEY] = 2  # stale + replacement holders
        pool.release(stale, self.HOST, self.PORT, self.USER)
        self.assertTrue(stale.closed)
        self.assertFalse(replacement.closed)
        with pool._lock:
            self.assertIs(pool._pool[self.KEY][0], replacement)
        pool.release(replacement, self.HOST, self.PORT, self.USER)

    def test_evict_keeps_replacement(self):
        """Evicting a failed client must leave a replacement connection
        (which belongs to another in-flight command) in the pool."""
        pool = make_pool()
        stale = pool.get(self.HOST, self.PORT, self.USER)
        stale.transport.active = False
        replacement = FakeClient()
        with pool._lock:
            pool._pool[self.KEY] = (replacement, time.time())
            pool._refcounts[self.KEY] = 2
        pool.evict(stale, self.HOST, self.PORT, self.USER)
        self.assertTrue(stale.closed)
        self.assertFalse(replacement.closed)
        with pool._lock:
            self.assertIs(pool._pool[self.KEY][0], replacement)

    def test_eviction_skips_in_use_connections(self):
        pool = make_pool(max_connections=2)
        held = {}
        for i in range(2):
            key = (f"host{i}", 22, "root")
            held[key] = pool.get(key[0], 22, "root")
        # Both pooled connections are in use: adding a third must not evict
        # them (the pool may temporarily exceed max_connections).
        key3 = ("host3", 22, "root")
        held[key3] = pool.get(key3[0], 22, "root")
        with pool._lock:
            self.assertEqual(len(pool._pool), 3)
            self.assertTrue(all(not c.closed for c in held.values()))
        for key, client in held.items():
            pool.release(client, key[0], 22, "root")
        # Now everything is idle: the oldest connection is evicted when a
        # new one is added.
        key4 = ("host4", 22, "root")
        fourth = pool.get(key4[0], 22, "root")
        with pool._lock:
            self.assertEqual(len(pool._pool), 3)
            self.assertNotIn(("host0", 22, "root"), pool._pool)
        pool.release(fourth, key4[0], 22, "root")

    def test_create_failure_releases_lock_and_refcount(self):
        pool = make_pool(lock_timeout=1.0)

        def boom(*args, **kwargs):
            raise OSError("connect refused")

        pool._create_client = boom
        with self.assertRaises(OSError):
            pool.get(self.HOST, self.PORT, self.USER)
        # The lock must be free again and the refcount balanced: a later
        # get() works once creation succeeds.
        pool._create_client = lambda *a, **k: FakeClient()
        client = pool.get(self.HOST, self.PORT, self.USER)
        self.assertIsInstance(client, FakeClient)
        pool.release(client, self.HOST, self.PORT, self.USER)
        with pool._lock:
            self.assertEqual(pool._refcounts.get(self.KEY, 0), 0)


if __name__ == "__main__":
    unittest.main()
