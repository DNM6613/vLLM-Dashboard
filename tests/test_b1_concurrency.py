"""Concurrency regression: SSH pool refcount/evict + session manager.

Threading-based: concurrent get / release / evict on the SSH connection pool
and concurrent get_or_create / attach on the console session manager must
not leak connections, corrupt refcounts, or kill in-use resources.
"""
import threading
import time
import unittest
from types import SimpleNamespace

from backend.console.session_manager import ConsoleSessionManager
from backend.executor.remote.ssh import SSHConnectionPool

HOST, PORT, USER = "10.7.7.7", 22, "root"
KEY = (HOST, PORT, USER)


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


class _FakeSession:
    def __init__(self, name):
        self.name = name
        self.active_ws = 0
        self.close_count = 0

    def is_alive(self):
        return True

    def close(self):
        self.close_count += 1


def _make_pool(create_fn=None, **kwargs):
    pool = SSHConnectionPool(**kwargs)
    pool._create_client = create_fn or (lambda *a, **k: FakeClient())
    return pool


class TestPoolConcurrency(unittest.TestCase):
    def test_concurrent_get_same_host_yields_one_connection(self):
        creations = []
        lock = threading.Lock()

        def slow_create(*a, **k):
            with lock:
                creations.append(1)
            time.sleep(0.05)
            return FakeClient()

        pool = _make_pool(slow_create, lock_timeout=10)
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(pool.get(HOST, PORT, USER))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 8)
        self.assertEqual(len({id(c) for c in results}), 1)  # all share one client
        self.assertEqual(len(creations), 1)
        with pool._lock:
            self.assertEqual(pool._refcounts.get(KEY, 0), 8)  # all holders registered
        for client in results:
            pool.release(client, HOST, PORT, USER)
        with pool._lock:
            self.assertEqual(pool._refcounts.get(KEY, 0), 0)  # no refcount leak
            self.assertEqual(len(pool._pool), 1)
            self.assertFalse(results[0].closed)

    def test_repeated_bursts_do_not_leak(self):
        pool = _make_pool(lock_timeout=10)
        for _round in range(10):
            clients = [pool.get(HOST, PORT, USER) for _ in range(4)]
            for c in clients:
                pool.release(c, HOST, PORT, USER)
        with pool._lock:
            self.assertEqual(pool._refcounts.get(KEY, 0), 0)
            self.assertEqual(len(pool._pool), 1)

    def test_capacity_eviction_never_closes_in_use_connections(self):
        pool = _make_pool(max_connections=2, lock_timeout=10)
        held = {}
        for i in range(2):
            key = (f"host{i}", 22, "root")
            held[key] = pool.get(key[0], 22, "root")
        # third connection forces eviction of an idle one; both in-use
        # connections must survive untouched
        third = pool.get("host2", 22, "root")
        with pool._lock:
            self.assertGreaterEqual(len(pool._pool), 2)
            for key, client in held.items():
                self.assertIn(key, pool._pool)
                self.assertFalse(client.closed)
        for key, client in held.items():
            pool.release(client, key[0], 22, "root")
        pool.release(third, "host2", 22, "root")

    def test_create_failure_keeps_refcount_balanced_under_concurrency(self):
        state = {"fail": True}
        lock = threading.Lock()

        def flaky(*a, **k):
            with lock:
                fail = state["fail"]
            if fail:
                raise OSError("connect refused")
            return FakeClient()

        pool = _make_pool(flaky, lock_timeout=10)
        errors = []
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            try:
                pool.get(HOST, PORT, USER)
            except OSError:
                errors.append(1)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 4)
        with pool._lock:
            self.assertEqual(pool._refcounts.get(KEY, 0), 0)  # failures rolled back
        state["fail"] = False
        client = pool.get(HOST, PORT, USER)  # pool recovers after success
        self.assertIsInstance(client, FakeClient)
        pool.release(client, HOST, PORT, USER)


class TestSessionManagerConcurrency(unittest.TestCase):
    def _manager_with_fake_create(self, delay=0.05):
        m = ConsoleSessionManager()
        create_lock = threading.Lock()
        created = []

        def slow_create(config):
            with create_lock:
                created.append(config.host)
            time.sleep(delay)
            return _FakeSession(config.host)

        m._create = slow_create
        return m, created

    def _config(self, host):
        return SimpleNamespace(host=host, ssh_port=22, ssh_username="root",
                               ssh_password="pw")

    def test_concurrent_get_or_create_same_config_yields_one_session(self):
        m, created = self._manager_with_fake_create()
        results = []
        barrier = threading.Barrier(6)

        def worker():
            barrier.wait()
            results.append(m.get_or_create(self._config("10.7.7.8")))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len({id(s) for s, _ in results}), 1)
        self.assertEqual(len(created), 1)
        self.assertEqual(m.active_session_count(), 1)
        session = results[0][0]
        self.assertFalse(session.close_count)  # nobody was killed

    def test_distinct_sessions_at_capacity_are_all_kept_alive(self):
        m, _created = self._manager_with_fake_create(delay=0.01)
        attached = []
        for i in range(m._MAX_SESSIONS):
            session, is_new = m.get_or_create(self._config(f"10.7.7.{10 + i}"))
            self.assertTrue(is_new)
            m.attach(session)
            attached.append(session)

        # capacity full and every session attached: a new one is refused,
        # none of the live sessions is evicted/killed
        refused, is_new = m.get_or_create(self._config("10.7.7.99"))
        self.assertIsNone(refused)
        self.assertFalse(is_new)
        for s in attached:
            self.assertTrue(s.is_alive())
            self.assertEqual(s.close_count, 0)
            m.detach(s)
        self.assertEqual(m.active_session_count(), m._MAX_SESSIONS)


if __name__ == "__main__":
    unittest.main()
