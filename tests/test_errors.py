"""500 responses must not leak internal details outside development."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.endpoints.inspection import _server_error  # noqa: E402
from app.core.config import settings  # noqa: E402


class ServerError(unittest.TestCase):
    def setUp(self):
        self.env = settings.APP_ENV

    def tearDown(self):
        settings.APP_ENV = self.env

    def test_details_only_in_development(self):
        err = Exception('connection to "db.internal" refused')
        settings.APP_ENV = "development"
        self.assertIn("db.internal", _server_error("Failed to save", err).detail)
        settings.APP_ENV = "production"
        self.assertNotIn("db.internal", _server_error("Failed to save", err).detail)

    def test_user_facing_messages_always_shown(self):
        settings.APP_ENV = "production"
        e = _server_error("Failed", RuntimeError("The inspection was not saved."))
        self.assertEqual((e.status_code, e.detail), (500, "The inspection was not saved."))


if __name__ == "__main__":
    unittest.main()
