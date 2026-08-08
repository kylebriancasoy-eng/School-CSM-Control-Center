from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.storage.scanner_security import ScannerAuthenticationError, ScannerOperatorStore


class ScannerOperatorSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.store = ScannerOperatorStore(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_password_is_hashed_and_not_returned(self) -> None:
        account = self.store.save_account(
            username="scanner01",
            display_name="Operator One",
            password="secret12",
        )
        self.assertNotIn("password_hash", account)
        document = json.loads(self.store.path.read_text("utf-8"))
        stored = document["accounts"][0]
        self.assertNotEqual(stored["password_hash"], "secret12")
        self.assertNotIn("secret12", self.store.path.read_text("utf-8"))
        authenticated = self.store.authenticate("scanner01", "secret12")
        self.assertEqual(authenticated["user_id"], account["user_id"])

    def test_failed_attempt_limit_temporarily_locks_account(self) -> None:
        self.store.save_account(
            username="scanner01",
            display_name="Operator One",
            password="secret12",
        )
        with self.assertRaises(ScannerAuthenticationError):
            self.store.authenticate("scanner01", "wrong", failed_login_limit=2, lockout_seconds=60)
        with self.assertRaises(ScannerAuthenticationError):
            self.store.authenticate("scanner01", "wrong", failed_login_limit=2, lockout_seconds=60)
        with self.assertRaises(ScannerAuthenticationError) as locked:
            self.store.authenticate("scanner01", "secret12", failed_login_limit=2, lockout_seconds=60)
        self.assertEqual(locked.exception.code, "account_locked")

    def test_archived_account_cannot_authenticate(self) -> None:
        account = self.store.save_account(
            username="scanner01",
            display_name="Operator One",
            password="secret12",
        )
        self.store.archive(account["user_id"])
        with self.assertRaises(ScannerAuthenticationError):
            self.store.authenticate("scanner01", "secret12")


if __name__ == "__main__":
    unittest.main()
