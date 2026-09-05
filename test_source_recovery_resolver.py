"""Tests for source_recovery_resolver.accept_recovery_candidate()/
dismiss_recovery_candidate(). Uses in-memory SQLite databases - never
touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_source_recovery_resolver -v
"""
import sqlite3
import unittest
from pathlib import Path

from source_recovery_resolver import accept_recovery_candidate, dismiss_recovery_candidate

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
RESOLVED_AT = "2026-09-06T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_event(conn, **overrides):
    fields = dict(
        name="Test Hack", source_url="https://old-site.example.com",
        consecutive_failures=3, last_check_status="unavailable",
        last_checked="2026-09-01T00:00:00+00:00", last_successful_check="2026-08-01T00:00:00+00:00",
        status="new", event_state="active",
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


def _insert_candidate(conn, event_id, candidate_url="https://mlh.io/events/test", status="pending", **overrides):
    fields = dict(
        event_id=event_id, candidate_url=candidate_url, candidate_type="mlh",
        title="Test Hack - MLH", evidence="Linked from source page", confidence="medium",
        discovered_at="2026-09-05T00:00:00+00:00", status=status, resolved_at=None,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(
        f"INSERT INTO source_recovery_candidates ({columns}) VALUES ({placeholders})", fields
    )
    conn.commit()
    return cur.lastrowid


def _event(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def _candidate(conn, candidate_id):
    return conn.execute(
        "SELECT * FROM source_recovery_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()


class _FlakyConn:
    def __init__(self, real_conn, fail_on):
        self._real = real_conn
        self._fail_on = fail_on

    def execute(self, sql, params=()):
        if self._fail_on in sql:
            raise sqlite3.OperationalError("simulated failure")
        return self._real.execute(sql, params)

    def commit(self):
        self._real.commit()

    def rollback(self):
        self._real.rollback()


class AcceptTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_accept_updates_source_url_only(self):
        cid = _insert_candidate(self.conn, self.event_id, candidate_url="https://new-site.example.com")
        before = _event(self.conn, self.event_id)
        accept_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)
        after = _event(self.conn, self.event_id)
        self.assertEqual(after["source_url"], "https://new-site.example.com")
        for field in before.keys():
            if field in ("source_url", "updated_at"):
                continue
            self.assertEqual(after[field], before[field], field)

    def test_accept_marks_candidate_accepted_with_resolved_at(self):
        cid = _insert_candidate(self.conn, self.event_id)
        accept_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)
        row = _candidate(self.conn, cid)
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["resolved_at"], RESOLVED_AT)

    def test_accept_updates_events_updated_at(self):
        self.conn.execute("UPDATE events SET updated_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (self.event_id,))
        self.conn.commit()
        cid = _insert_candidate(self.conn, self.event_id)
        accept_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, self.event_id)["updated_at"], RESOLVED_AT)

    def test_accept_preserves_source_health_history(self):
        cid = _insert_candidate(self.conn, self.event_id)
        accept_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)
        row = _event(self.conn, self.event_id)
        self.assertEqual(row["consecutive_failures"], 3)
        self.assertEqual(row["last_check_status"], "unavailable")
        self.assertEqual(row["last_checked"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(row["last_successful_check"], "2026-08-01T00:00:00+00:00")

    def test_accept_does_not_change_status_event_state_or_inactive_reason(self):
        cid = _insert_candidate(self.conn, self.event_id)
        accept_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)
        row = _event(self.conn, self.event_id)
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["event_state"], "active")
        self.assertIsNone(row["inactive_reason"])

    def test_cannot_accept_already_resolved(self):
        cid = _insert_candidate(self.conn, self.event_id, status="accepted")
        with self.assertRaises(ValueError):
            accept_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)

    def test_nonexistent_candidate_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            accept_recovery_candidate(self.conn, 99999, resolved_at=RESOLVED_AT)

    def test_accept_rolls_back_source_url_if_candidate_update_fails(self):
        cid = _insert_candidate(self.conn, self.event_id, candidate_url="https://new-site.example.com")
        flaky = _FlakyConn(self.conn, "UPDATE source_recovery_candidates SET status = 'accepted'")
        with self.assertRaises(sqlite3.OperationalError):
            accept_recovery_candidate(flaky, cid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, self.event_id)["source_url"], "https://old-site.example.com")
        self.assertEqual(_candidate(self.conn, cid)["status"], "pending")


class DismissTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_dismiss_leaves_source_url_unchanged(self):
        cid = _insert_candidate(self.conn, self.event_id, candidate_url="https://new-site.example.com")
        dismiss_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, self.event_id)["source_url"], "https://old-site.example.com")

    def test_dismiss_sets_status_and_resolved_at(self):
        cid = _insert_candidate(self.conn, self.event_id)
        dismiss_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)
        row = _candidate(self.conn, cid)
        self.assertEqual(row["status"], "dismissed")
        self.assertEqual(row["resolved_at"], RESOLVED_AT)

    def test_cannot_dismiss_already_resolved(self):
        cid = _insert_candidate(self.conn, self.event_id, status="dismissed")
        with self.assertRaises(ValueError):
            dismiss_recovery_candidate(self.conn, cid, resolved_at=RESOLVED_AT)

    def test_nonexistent_candidate_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            dismiss_recovery_candidate(self.conn, 99999, resolved_at=RESOLVED_AT)

    def test_dismiss_rolls_back_on_failure(self):
        cid = _insert_candidate(self.conn, self.event_id)
        flaky = _FlakyConn(self.conn, "UPDATE source_recovery_candidates SET status = 'dismissed'")
        with self.assertRaises(sqlite3.OperationalError):
            dismiss_recovery_candidate(flaky, cid, resolved_at=RESOLVED_AT)
        self.assertEqual(_candidate(self.conn, cid)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
