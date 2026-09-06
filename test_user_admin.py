"""Tests for user_admin.py (V0.10C account-management CLI). Mocks
getpass.getpass - never uses a real terminal or a real secret. Runs
against a temporary SQLite file (db.DB_PATH monkeypatched) - never
touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_user_admin -v
"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import db as db_module
import user_admin


class UserAdminTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self._tmpdir.name) / "events.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _conn(self):
        return db_module.get_db()

    def _run(self, argv, passwords=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            if passwords is not None:
                with patch("getpass.getpass", side_effect=passwords):
                    code = user_admin.main(argv)
            else:
                code = user_admin.main(argv)
        return code, buf.getvalue()


class CreateUserCommandTests(UserAdminTestCase):
    def test_creates_user_with_hashed_password(self):
        code, _ = self._run(
            ["create-user", "--email", "friend@example.com", "--display-name", "Friend"],
            passwords=["s3cret-pass", "s3cret-pass"],
        )
        self.assertEqual(code, 0)
        conn = self._conn()
        row = conn.execute("SELECT * FROM users WHERE email = 'friend@example.com'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["password_hash"])
        self.assertNotIn("s3cret-pass", row["password_hash"])

    def test_default_created_account_is_non_admin(self):
        self._run(["create-user", "--email", "friend@example.com", "--display-name", "Friend"], passwords=["s3cret-pass", "s3cret-pass"])
        conn = self._conn()
        row = conn.execute("SELECT is_admin FROM users WHERE email = 'friend@example.com'").fetchone()
        conn.close()
        self.assertEqual(row["is_admin"], 0)

    def test_admin_flag_creates_admin(self):
        self._run(
            ["create-user", "--email", "admin2@example.com", "--admin", "--display-name", "Admin Two"],
            passwords=["s3cret-pass", "s3cret-pass"],
        )
        conn = self._conn()
        row = conn.execute("SELECT is_admin FROM users WHERE email = 'admin2@example.com'").fetchone()
        conn.close()
        self.assertEqual(row["is_admin"], 1)

    def test_mismatched_confirmation_creates_no_user(self):
        code, _ = self._run(
            ["create-user", "--email", "friend@example.com", "--display-name", "Friend"],
            passwords=["s3cret-pass", "different-pass"],
        )
        self.assertEqual(code, 1)
        conn = self._conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE email = 'friend@example.com'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_duplicate_normalized_email_rejected(self):
        self._run(["create-user", "--email", "friend@example.com", "--display-name", "Friend"], passwords=["s3cret-pass", "s3cret-pass"])
        code, _ = self._run(
            ["create-user", "--email", "Friend@Example.com", "--display-name", "Friend Dup"], passwords=["other-pass", "other-pass"]
        )
        self.assertEqual(code, 1)
        conn = self._conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE email = 'friend@example.com'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


class SetPasswordCommandTests(UserAdminTestCase):
    def test_updates_hash(self):
        self._run(["create-user", "--email", "friend@example.com", "--display-name", "Friend"], passwords=["s3cret-pass", "s3cret-pass"])
        conn = self._conn()
        old_hash = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'friend@example.com'"
        ).fetchone()["password_hash"]
        conn.close()

        code, _ = self._run(["set-password", "--email", "friend@example.com"], passwords=["new-pass", "new-pass"])
        self.assertEqual(code, 0)
        conn = self._conn()
        new_hash = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'friend@example.com'"
        ).fetchone()["password_hash"]
        conn.close()
        self.assertNotEqual(old_hash, new_hash)

    def test_can_activate_null_hash_bootstrap_owner(self):
        self._run(["list-users"])  # triggers init_db() -> bootstrap owner exists, NULL hash
        code, _ = self._run(
            ["set-password", "--email", "owner@event-radar.local"],
            passwords=["new-owner-pass", "new-owner-pass"],
        )
        self.assertEqual(code, 0)
        conn = self._conn()
        row = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'owner@event-radar.local'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row["password_hash"])

    def test_nonexistent_email_fails_clearly_without_prompting(self):
        self._run(["list-users"])
        code, _ = self._run(["set-password", "--email", "nobody@example.com"])
        self.assertEqual(code, 1)

    def test_mismatched_confirmation_leaves_hash_unchanged(self):
        self._run(["create-user", "--email", "friend@example.com", "--display-name", "Friend"], passwords=["s3cret-pass", "s3cret-pass"])
        conn = self._conn()
        old_hash = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'friend@example.com'"
        ).fetchone()["password_hash"]
        conn.close()

        code, _ = self._run(
            ["set-password", "--email", "friend@example.com"], passwords=["a-pass", "different-pass"]
        )
        self.assertEqual(code, 1)
        conn = self._conn()
        unchanged_hash = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'friend@example.com'"
        ).fetchone()["password_hash"]
        conn.close()
        self.assertEqual(old_hash, unchanged_hash)


class ListUsersCommandTests(UserAdminTestCase):
    def test_does_not_reveal_password_hashes(self):
        self._run(["create-user", "--email", "friend@example.com", "--display-name", "Friend"], passwords=["s3cret-pass", "s3cret-pass"])
        code, output = self._run(["list-users"])
        self.assertEqual(code, 0)
        self.assertIn("friend@example.com", output)
        conn = self._conn()
        real_hash = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'friend@example.com'"
        ).fetchone()["password_hash"]
        conn.close()
        self.assertNotIn(real_hash, output)

    def test_shows_admin_and_password_configured_columns(self):
        self._run(["create-user", "--email", "friend@example.com", "--display-name", "Friend"], passwords=["s3cret-pass", "s3cret-pass"])
        code, output = self._run(["list-users"])
        self.assertEqual(code, 0)
        self.assertIn("owner@event-radar.local", output)  # bootstrap owner, NULL hash
        self.assertIn("friend@example.com", output)


if __name__ == "__main__":
    unittest.main()
