"""Console session state machine regression.

Locks the ConsoleSessionManager state transitions and invariants around
create / attach / evict / close:

* get_or_create: create-once + reuse, failed create clears the _CREATING slot
* capacity eviction (_evict_one_locked via get_or_create): never kills a
  session with an attached WebSocket, refuses when nothing is evictable
* close: idempotent, flips is_alive
* attach/detach: active_ws counting is thread-safe and never negative
"""
import threading
import unittest

from backend.console.session import ConsoleSession
from backend.console.session_manager import ConsoleSessionManager


class _FakeSession:
    """Duck-typed session for manager-level tests (no SSH/threads)."""

    def __init__(self, key="s", alive=True):
        self.key = key
        self._alive = alive
        self.active_ws = 0
        self.close_count = 0

    def is_alive(self):
        return self._alive

    def close(self):
        self._alive = False
        self.close_count += 1


class _FakeChannel:
    def __init__(self):
        self.closed = False
        self.close_count = 0

    def close(self):
        self.closed = True
        self.close_count += 1


class _FakeSSH:
    def __init__(self):
        self.closed = False
        self.close_count = 0

    def close(self):
        self.closed = True
        self.close_count += 1


def _config(host="10.9.9.9", user="root", pw="pw", port=22):
    from types import SimpleNamespace
    return SimpleNamespace(host=host, ssh_port=port, ssh_username=user, ssh_password=pw)


class TestGetOrCreateTransitions(unittest.TestCase):
    def test_creates_once_then_reuses(self):
        m = ConsoleSessionManager()
        created = []
        m._create = lambda config: created.append(_FakeSession()) or created[-1]

        session, is_new = m.get_or_create(_config())
        self.assertTrue(is_new)
        self.assertIn(session, m._sessions.values())
        again, is_new2 = m.get_or_create(_config())
        self.assertFalse(is_new2)
        self.assertIs(again, session)
        self.assertEqual(len(created), 1)

    def test_failed_create_clears_creating_slot_and_can_retry(self):
        m = ConsoleSessionManager()
        calls = []

        def flaky(config):
            calls.append(1)
            return None if len(calls) == 1 else _FakeSession()

        m._create = flaky
        session, is_new = m.get_or_create(_config())
        self.assertIsNone(session)
        self.assertFalse(is_new)
        self.assertNotIn(m._key(_config()), m._sessions)  # slot released
        retry, is_new2 = m.get_or_create(_config())
        self.assertTrue(is_new2)
        self.assertIsNotNone(retry)
        self.assertEqual(len(calls), 2)

    def test_capacity_refuses_when_all_sessions_attached(self):
        m = ConsoleSessionManager()
        for i in range(m._MAX_SESSIONS):
            m._sessions[f"k{i}"] = _FakeSession()
            m.attach(m._sessions[f"k{i}"])
        m._create = lambda config: _FakeSession()
        session, is_new = m.get_or_create(_config(host="10.9.9.10"))
        self.assertIsNone(session)
        self.assertFalse(is_new)
        # refusal must not have disturbed the live sessions
        self.assertEqual(len(m._sessions), m._MAX_SESSIONS)
        self.assertTrue(all(s.is_alive() for s in m._sessions.values()))

    def test_capacity_evicts_unattached_session_only(self):
        m = ConsoleSessionManager()
        victim = _FakeSession()
        protected = _FakeSession()
        m._sessions["victim"] = victim
        m._sessions["protected"] = protected
        m.attach(protected)
        # fill the pool to capacity so get_or_create must evict
        for i in range(m._MAX_SESSIONS - 2):
            m._sessions[f"filler{i}"] = _FakeSession()
        m._create = lambda config: _FakeSession()

        session, is_new = m.get_or_create(_config(host="10.9.9.10"))
        self.assertTrue(is_new)
        self.assertIsNotNone(session)
        self.assertGreaterEqual(victim.close_count, 1)  # evicted and closed
        self.assertNotIn("victim", m._sessions)
        self.assertIn("protected", m._sessions)      # attached session survives
        self.assertEqual(protected.close_count, 0)
        self.assertTrue(protected.is_alive())


class TestConcurrentGetOrCreate(unittest.TestCase):
    def test_single_flight_creates_one_session(self):
        m = ConsoleSessionManager()
        create_lock = threading.Lock()
        created = []

        def slow_create(config):
            with create_lock:
                created.append("c")
            import time
            time.sleep(0.1)
            return _FakeSession()

        m._create = slow_create
        results = []
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            results.append(m.get_or_create(_config()))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 4)
        sessions = {id(s) for s, _ in results}
        self.assertEqual(len(sessions), 1)           # all share one session
        self.assertEqual(sum(1 for _, new in results if new), 1)  # exactly one creator
        self.assertEqual(len(created), 1)
        self.assertEqual(m.active_session_count(), 1)

    def test_concurrent_attach_detach_balance(self):
        m = ConsoleSessionManager()
        s = _FakeSession()
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            m.attach(s)
            m.detach(s)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(s.active_ws, 0)
        self.assertTrue(s.is_alive())


class TestCloseSemantics(unittest.TestCase):
    def test_close_is_idempotent_and_flips_alive(self):
        channel = _FakeChannel()
        ssh = _FakeSSH()
        session = ConsoleSession(ssh, channel)
        self.assertTrue(session.is_alive())
        session.close()
        session.close()
        self.assertFalse(session.is_alive())
        self.assertTrue(channel.closed)
        self.assertTrue(ssh.closed)
        # repeated close must not raise (channel/ssh close is idempotent)
        self.assertGreaterEqual(channel.close_count, 1)
        self.assertGreaterEqual(ssh.close_count, 1)

    def test_active_session_count_excludes_creating_placeholder(self):
        m = ConsoleSessionManager()
        m._sessions["live"] = _FakeSession()
        m._sessions["busy"] = m._CREATING
        self.assertEqual(m.active_session_count(), 1)


if __name__ == "__main__":
    unittest.main()
