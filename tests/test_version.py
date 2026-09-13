import unittest

from backend import __version__
from backend.main import app


class TestVersion(unittest.TestCase):
    def test_version_is_three_part_numeric(self):
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertTrue(part.isdigit(), f"non-numeric version part: {part!r}")

    def test_fastapi_app_uses_single_source(self):
        self.assertEqual(app.version, __version__)

    def test_health_returns_single_source(self):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], __version__)

if __name__ == "__main__":
    unittest.main()
