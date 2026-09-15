import unittest

from backend.console.session_manager import ConsoleSessionManager


class _FakeSession:
    """Duck-type of ConsoleSession for eviction/purge unit tests.

    Exposes only what ConsoleSessionManager touches: is_alive(), close() and
    the active_ws counter (mutated under the manager's lock).
    """

    def __init__(self, alive: bool = True, active_ws: int = 0):
        self._alive = alive
        self.active_ws = active_ws
        self.closed = False

    def is_alive(self) -> bool:
        return self._alive

    def close(self) -> None:
        self._alive = False
        self.closed = True


class TestEvictOneLocked(unittest.TestCase):
    def test_evicts_an_inactive_session(self):
        m = ConsoleSessionManager()
        s = _FakeSession(alive=True, active_ws=0)
        m._sessions["k1"] = s
        self.assertTrue(m._evict_one_locked())
        self.assertTrue(s.closed)
        self.assertNotIn("k1", m._sessions)

    def test_refuses_when_all_sessions_have_active_ws(self):
        m = ConsoleSessionManager()
        m._sessions["k1"] = _FakeSession(alive=True, active_ws=1)
        m._sessions["k2"] = _FakeSession(alive=True, active_ws=2)
        self.assertFalse(m._evict_one_locked())
        self.assertIn("k1", m._sessions)
        self.assertIn("k2", m._sessions)

    def test_skips_creating_placeholder(self):
        m = ConsoleSessionManager()
        m._sessions["creating"] = m._CREATING
        evictable = _FakeSession(alive=True, active_ws=0)
        m._sessions["k1"] = evictable
        self.assertTrue(m._evict_one_locked())
        self.assertTrue(evictable.closed)
        # The _CREATING placeholder is untouched; only the real session left.
        self.assertEqual(m._sessions, {"creating": m._CREATING})

    def test_evicts_only_first_evictable(self):
        m = ConsoleSessionManager()
        m._sessions["k1"] = _FakeSession(alive=True, active_ws=1)
        victim = _FakeSession(alive=True, active_ws=0)
        m._sessions["k2"] = victim
        self.assertTrue(m._evict_one_locked())
        self.assertTrue(victim.closed)
        # k1 (active_ws>0) is still present and was not closed.
        self.assertIn("k1", m._sessions)
        self.assertNotIn("k2", m._sessions)

    def test_returns_false_when_no_sessions(self):
        m = ConsoleSessionManager()
        self.assertFalse(m._evict_one_locked())


class TestPurgeStale(unittest.TestCase):
    def test_removes_dead_sessions_only(self):
        m = ConsoleSessionManager()
        dead = _FakeSession(alive=False)
        live = _FakeSession(alive=True)
        m._sessions["dead"] = dead
        m._sessions["live"] = live
        m._sessions["creating"] = m._CREATING
        m._purge_stale()
        self.assertTrue(dead.closed)
        self.assertNotIn("dead", m._sessions)
        self.assertIn("live", m._sessions)
        self.assertIn("creating", m._sessions)


class TestAttachDetach(unittest.TestCase):
    def test_attach_increments_detach_decrements(self):
        m = ConsoleSessionManager()
        s = _FakeSession(active_ws=0)
        m.attach(s)
        m.attach(s)
        self.assertEqual(s.active_ws, 2)
        m.detach(s)
        self.assertEqual(s.active_ws, 1)

    def test_detach_never_goes_negative(self):
        m = ConsoleSessionManager()
        s = _FakeSession(active_ws=0)
        m.detach(s)
        self.assertEqual(s.active_ws, 0)


if __name__ == "__main__":
    unittest.main()
