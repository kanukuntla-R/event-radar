"""Tests for user_store.py: users, per-user event status, and calendar
tokens (V0.10A multi-user foundation). In-memory SQLite only - never
touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_user_store -v
"""
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from user_store import (
    BOOTSTRAP_OWNER_EMAIL, BOOTSTRAP_OWNER_DISPLAY_NAME,
    get_user_by_email, get_user_by_id, get_or_create_bootstrap_owner,
    get_user_event_status, set_user_event_status, create_calendar_token,
    normalize_email, create_user, set_user_password, verify_user_password,
)

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_event(conn, **overrides):
    fields = dict(name="Test Event", source="ASU Events", source_url="https://example.com/e", status="new")
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


class _WrongIntegrityErrorConn:
    """Wraps a real connection but replaces the calendar_tokens INSERT
    with an unrelated IntegrityError - proves the collision retry doesn't
    swallow every IntegrityError as if it were a token collision."""

    def __init__(self, real_conn):
        self._real = real_conn

    def execute(self, sql, params=()):
        if "INSERT INTO calendar_tokens" in sql:
            raise sqlite3.IntegrityError("NOT NULL constraint failed: calendar_tokens.user_id")
        return self._real.execute(sql, params)

    def commit(self):
        self._real.commit()

    def rollback(self):
        self._real.rollback()


class BootstrapOwnerTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_creates_owner_when_no_users_exist(self):
        user = get_or_create_bootstrap_owner(self.conn)
        self.assertEqual(user["email"], BOOTSTRAP_OWNER_EMAIL)
        self.assertEqual(user["display_name"], BOOTSTRAP_OWNER_DISPLAY_NAME)
        self.assertIsNone(user["password_hash"])

    def test_freshly_created_owner_is_admin(self):
        user = get_or_create_bootstrap_owner(self.conn)
        self.assertEqual(user["is_admin"], 1)

    def test_is_idempotent(self):
        first = get_or_create_bootstrap_owner(self.conn)
        second = get_or_create_bootstrap_owner(self.conn)
        self.assertEqual(first["id"], second["id"])
        count = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 1)

    def test_get_user_by_email_finds_created_owner(self):
        get_or_create_bootstrap_owner(self.conn)
        self.assertIsNotNone(get_user_by_email(self.conn, BOOTSTRAP_OWNER_EMAIL))

    def test_get_user_by_email_returns_none_for_missing(self):
        self.assertIsNone(get_user_by_email(self.conn, "nobody@example.com"))

    def test_does_not_commit_internally(self):
        # Callers that need owner-creation to be part of a larger atomic
        # transaction (db.py's migration) must be able to roll it back.
        get_or_create_bootstrap_owner(self.conn)
        self.conn.rollback()
        self.assertIsNone(get_user_by_email(self.conn, BOOTSTRAP_OWNER_EMAIL))


class EmailNormalizationTests(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(normalize_email("User@Example.com"), "user@example.com")

    def test_strips_whitespace(self):
        self.assertEqual(normalize_email("  user@example.com  "), "user@example.com")

    def test_lookup_is_case_and_whitespace_insensitive(self):
        conn = _make_conn()
        try:
            create_user(conn, "Friend@Example.com", "s3cret-pass")
            conn.commit()
            self.assertIsNotNone(get_user_by_email(conn, "  friend@example.com  "))
        finally:
            conn.close()


class CreateUserTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_creates_normalized_non_admin_user(self):
        user = create_user(self.conn, "Friend@Example.com", "s3cret-pass")
        self.assertEqual(user["email"], "friend@example.com")
        self.assertEqual(user["is_admin"], 0)

    def test_password_is_hashed_not_plaintext(self):
        user = create_user(self.conn, "friend@example.com", "s3cret-pass")
        self.assertIsNotNone(user["password_hash"])
        self.assertNotIn("s3cret-pass", user["password_hash"])

    def test_can_create_admin_explicitly(self):
        user = create_user(self.conn, "admin2@example.com", "s3cret-pass", is_admin=True)
        self.assertEqual(user["is_admin"], 1)

    def test_duplicate_normalized_email_rejected(self):
        create_user(self.conn, "friend@example.com", "s3cret-pass")
        with self.assertRaises(ValueError):
            create_user(self.conn, "Friend@Example.com ", "other-pass")
        count = self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 1)

    def test_does_not_commit_internally(self):
        create_user(self.conn, "friend@example.com", "s3cret-pass")
        self.conn.rollback()
        self.assertIsNone(get_user_by_email(self.conn, "friend@example.com"))


class SetUserPasswordTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.user = create_user(self.conn, "friend@example.com", "old-pass")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_updates_hash(self):
        old_hash = self.user["password_hash"]
        set_user_password(self.conn, self.user["id"], "new-pass")
        row = get_user_by_id(self.conn, self.user["id"])
        self.assertNotEqual(row["password_hash"], old_hash)

    def test_can_activate_a_null_hash_account(self):
        owner = get_or_create_bootstrap_owner(self.conn)
        self.conn.commit()
        self.assertIsNone(owner["password_hash"])
        set_user_password(self.conn, owner["id"], "new-owner-pass")
        row = get_user_by_id(self.conn, owner["id"])
        self.assertIsNotNone(row["password_hash"])

    def test_rejects_nonexistent_user(self):
        with self.assertRaises(ValueError):
            set_user_password(self.conn, self.user["id"] + 999, "new-pass")

    def test_does_not_commit_internally(self):
        set_user_password(self.conn, self.user["id"], "new-pass")
        self.conn.rollback()
        row = get_user_by_id(self.conn, self.user["id"])
        self.assertEqual(row["password_hash"], self.user["password_hash"])


class VerifyUserPasswordTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.user = create_user(self.conn, "friend@example.com", "correct-pass")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_correct_password_succeeds(self):
        result = verify_user_password(self.conn, "friend@example.com", "correct-pass")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], self.user["id"])

    def test_wrong_password_fails(self):
        self.assertIsNone(verify_user_password(self.conn, "friend@example.com", "wrong-pass"))

    def test_nonexistent_email_fails(self):
        self.assertIsNone(verify_user_password(self.conn, "nobody@example.com", "whatever"))

    def test_null_password_hash_cannot_authenticate(self):
        owner = get_or_create_bootstrap_owner(self.conn)
        self.conn.commit()
        self.assertIsNone(verify_user_password(self.conn, owner["email"], ""))
        self.assertIsNone(verify_user_password(self.conn, owner["email"], "anything"))

    def test_email_lookup_is_normalized(self):
        result = verify_user_password(self.conn, "  Friend@Example.com  ", "correct-pass")
        self.assertIsNotNone(result)


class GetUserByIdTests(unittest.TestCase):
    def test_returns_user(self):
        conn = _make_conn()
        try:
            user = create_user(conn, "friend@example.com", "s3cret-pass")
            self.assertEqual(get_user_by_id(conn, user["id"])["email"], "friend@example.com")
        finally:
            conn.close()

    def test_returns_none_for_missing(self):
        conn = _make_conn()
        try:
            self.assertIsNone(get_user_by_id(conn, 99999))
        finally:
            conn.close()


class UserEventStatusTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.owner = get_or_create_bootstrap_owner(self.conn)
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_get_returns_none_when_no_row(self):
        self.assertIsNone(get_user_event_status(self.conn, self.owner["id"], self.event_id))

    def test_set_inserts_new_row(self):
        set_user_event_status(self.conn, self.owner["id"], self.event_id, "interested")
        self.assertEqual(get_user_event_status(self.conn, self.owner["id"], self.event_id), "interested")

    def test_set_updates_existing_row_without_duplicating(self):
        set_user_event_status(self.conn, self.owner["id"], self.event_id, "interested")
        set_user_event_status(self.conn, self.owner["id"], self.event_id, "watching")
        self.assertEqual(get_user_event_status(self.conn, self.owner["id"], self.event_id), "watching")
        count = self.conn.execute(
            "SELECT COUNT(*) FROM user_event_status WHERE user_id = ? AND event_id = ?",
            (self.owner["id"], self.event_id),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_set_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            set_user_event_status(self.conn, self.owner["id"], self.event_id, "bogus")
        self.assertIsNone(get_user_event_status(self.conn, self.owner["id"], self.event_id))

    def test_created_at_preserved_on_update(self):
        set_user_event_status(self.conn, self.owner["id"], self.event_id, "interested")
        row1 = self.conn.execute(
            "SELECT created_at FROM user_event_status WHERE user_id = ? AND event_id = ?",
            (self.owner["id"], self.event_id),
        ).fetchone()
        set_user_event_status(self.conn, self.owner["id"], self.event_id, "watching")
        row2 = self.conn.execute(
            "SELECT created_at FROM user_event_status WHERE user_id = ? AND event_id = ?",
            (self.owner["id"], self.event_id),
        ).fetchone()
        self.assertEqual(row1["created_at"], row2["created_at"])

    def test_updated_at_changes_on_update(self):
        set_user_event_status(self.conn, self.owner["id"], self.event_id, "interested")
        row1 = self.conn.execute(
            "SELECT updated_at FROM user_event_status WHERE user_id = ? AND event_id = ?",
            (self.owner["id"], self.event_id),
        ).fetchone()
        set_user_event_status(self.conn, self.owner["id"], self.event_id, "watching")
        row2 = self.conn.execute(
            "SELECT updated_at FROM user_event_status WHERE user_id = ? AND event_id = ?",
            (self.owner["id"], self.event_id),
        ).fetchone()
        self.assertNotEqual(row1["updated_at"], row2["updated_at"])

    def test_does_not_mutate_events_status(self):
        set_user_event_status(self.conn, self.owner["id"], self.event_id, "interested")
        row = self.conn.execute("SELECT status FROM events WHERE id = ?", (self.event_id,)).fetchone()
        self.assertEqual(row["status"], "new")

    def test_rejects_nonexistent_user(self):
        bogus_user_id = self.owner["id"] + 999
        with self.assertRaises(ValueError):
            set_user_event_status(self.conn, bogus_user_id, self.event_id, "interested")
        count = self.conn.execute(
            "SELECT COUNT(*) FROM user_event_status WHERE event_id = ?", (self.event_id,)
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_rejects_nonexistent_event(self):
        bogus_event_id = self.event_id + 999
        with self.assertRaises(ValueError):
            set_user_event_status(self.conn, self.owner["id"], bogus_event_id, "interested")
        count = self.conn.execute(
            "SELECT COUNT(*) FROM user_event_status WHERE user_id = ?", (self.owner["id"],)
        ).fetchone()[0]
        self.assertEqual(count, 0)


class CalendarTokenTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.owner = get_or_create_bootstrap_owner(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_creates_token_row(self):
        token = create_calendar_token(self.conn, self.owner["id"])
        row = self.conn.execute("SELECT * FROM calendar_tokens WHERE token = ?", (token,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["user_id"], self.owner["id"])
        self.assertIsNone(row["revoked_at"])

    def test_tokens_are_unique_across_calls(self):
        t1 = create_calendar_token(self.conn, self.owner["id"])
        t2 = create_calendar_token(self.conn, self.owner["id"])
        self.assertNotEqual(t1, t2)

    def test_token_is_long_and_not_sequential(self):
        token = create_calendar_token(self.conn, self.owner["id"])
        self.assertGreaterEqual(len(token), 32)
        self.assertNotIn(token, ("1", "2", "token1", str(self.owner["id"])))

    def test_retries_on_token_collision(self):
        existing_token = create_calendar_token(self.conn, self.owner["id"])
        with patch("user_store.secrets.token_urlsafe", side_effect=[existing_token, "fresh-token-value"]):
            new_token = create_calendar_token(self.conn, self.owner["id"])
        self.assertEqual(new_token, "fresh-token-value")

    def test_rejects_nonexistent_user(self):
        bogus_user_id = self.owner["id"] + 999
        with self.assertRaises(ValueError):
            create_calendar_token(self.conn, bogus_user_id)
        count = self.conn.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        self.assertEqual(count, 0)

    def test_does_not_retry_for_nonexistent_user(self):
        bogus_user_id = self.owner["id"] + 999
        with patch("user_store.secrets.token_urlsafe") as mock_gen:
            with self.assertRaises(ValueError):
                create_calendar_token(self.conn, bogus_user_id)
        mock_gen.assert_not_called()

    def test_unrelated_integrity_error_is_not_treated_as_collision(self):
        flaky = _WrongIntegrityErrorConn(self.conn)
        with self.assertRaises(sqlite3.IntegrityError):
            create_calendar_token(flaky, self.owner["id"])
        count = self.conn.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
