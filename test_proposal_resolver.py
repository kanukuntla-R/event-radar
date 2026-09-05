"""Tests for proposal_resolver.accept_proposal()/dismiss_proposal(). Uses
in-memory SQLite databases - never touches data/events.db. Stdlib unittest
only.

Run with: python -m unittest test_proposal_resolver -v
"""
import sqlite3
import unittest
from pathlib import Path

from proposal_resolver import accept_proposal, dismiss_proposal, StaleProposalError

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
DETECTED_AT = "2026-09-05T00:00:00+00:00"
RESOLVED_AT = "2026-09-06T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_event(conn, **overrides):
    fields = dict(
        name="Test Hack", date_status="confirmed", start_date="2026-10-03",
        end_date="2026-10-04", registration_status="unknown", registration_url=None,
        application_deadline=None,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


def _insert_proposal(conn, event_id, field, old_value, new_value, status="pending", **overrides):
    fields = dict(
        event_id=event_id, field=field, old_value=old_value, new_value=new_value,
        evidence="apply today", source_result="ok", detected_at=DETECTED_AT,
        status=status, resolved_at=None,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(
        f"INSERT INTO event_change_proposals ({columns}) VALUES ({placeholders})", fields
    )
    conn.commit()
    return cur.lastrowid


def _event(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def _proposal(conn, proposal_id):
    return conn.execute(
        "SELECT * FROM event_change_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()


class _FlakyConn:
    """Delegates to a real connection but raises on the first execute() whose
    SQL contains `fail_on` - used to prove accept/dismiss roll back cleanly."""

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

    def test_accept_registration_status_unknown_to_open(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, self.event_id)["registration_status"], "open")
        self.assertEqual(_proposal(self.conn, pid)["status"], "accepted")

    def test_accept_registration_url_none_to_url(self):
        pid = _insert_proposal(
            self.conn, self.event_id, "registration_url", None, "https://example.com/apply"
        )
        accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, self.event_id)["registration_url"], "https://example.com/apply")

    def test_accept_date_field(self):
        event_id = _insert_event(self.conn, date_status="tba", start_date=None, end_date=None)
        pid = _insert_proposal(self.conn, event_id, "start_date", None, "2026-11-01")
        accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, event_id)["start_date"], "2026-11-01")

    def test_accept_updates_only_target_field(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        before = _event(self.conn, self.event_id)
        accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        after = _event(self.conn, self.event_id)
        for field in ("start_date", "end_date", "date_status", "registration_url", "application_deadline"):
            self.assertEqual(after[field], before[field])

    def test_accept_updates_events_updated_at(self):
        self.conn.execute("UPDATE events SET updated_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (self.event_id,))
        self.conn.commit()
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, self.event_id)["updated_at"], RESOLVED_AT)

    def test_accept_sets_resolved_at(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_proposal(self.conn, pid)["resolved_at"], RESOLVED_AT)


class DismissTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_dismiss_leaves_event_untouched(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        before = _event(self.conn, self.event_id)
        dismiss_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        after = _event(self.conn, self.event_id)
        self.assertEqual(after, before)

    def test_dismiss_sets_status_and_resolved_at(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        dismiss_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        row = _proposal(self.conn, pid)
        self.assertEqual(row["status"], "dismissed")
        self.assertEqual(row["resolved_at"], RESOLVED_AT)


class RepeatResolutionTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_cannot_accept_already_accepted(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open", status="accepted")
        with self.assertRaises(ValueError):
            accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)

    def test_cannot_dismiss_already_dismissed(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open", status="dismissed")
        with self.assertRaises(ValueError):
            dismiss_proposal(self.conn, pid, resolved_at=RESOLVED_AT)

    def test_cannot_dismiss_already_accepted(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open", status="accepted")
        with self.assertRaises(ValueError):
            dismiss_proposal(self.conn, pid, resolved_at=RESOLVED_AT)


class ErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_nonexistent_proposal_id_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            accept_proposal(self.conn, 99999, resolved_at=RESOLVED_AT)
        with self.assertRaises(LookupError):
            dismiss_proposal(self.conn, 99999, resolved_at=RESOLVED_AT)

    def test_unsupported_field_raises_value_error(self):
        pid = _insert_proposal(self.conn, self.event_id, "name", "Old Name", "New Name")
        with self.assertRaises(ValueError):
            accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)


class StaleProposalTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn, registration_status="closed")

    def tearDown(self):
        self.conn.close()

    def test_stale_conflict_raises_when_current_value_differs(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        with self.assertRaises(StaleProposalError):
            accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)

    def test_stale_conflict_leaves_event_unchanged(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        with self.assertRaises(StaleProposalError):
            accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, self.event_id)["registration_status"], "closed")

    def test_stale_conflict_leaves_proposal_pending(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        with self.assertRaises(StaleProposalError):
            accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        row = _proposal(self.conn, pid)
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["resolved_at"])


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_null_old_value_matches_empty_string_current_value(self):
        event_id = _insert_event(self.conn, registration_url="")
        pid = _insert_proposal(self.conn, event_id, "registration_url", None, "https://example.com/apply")
        accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, event_id)["registration_url"], "https://example.com/apply")

    def test_empty_string_old_value_matches_null_current_value(self):
        event_id = _insert_event(self.conn, registration_url=None)
        pid = _insert_proposal(self.conn, event_id, "registration_url", "", "https://example.com/apply")
        accept_proposal(self.conn, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, event_id)["registration_url"], "https://example.com/apply")


class TransactionRollbackTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_accept_rolls_back_event_update_if_proposal_update_fails(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        flaky = _FlakyConn(self.conn, "UPDATE event_change_proposals SET status = 'accepted'")
        with self.assertRaises(sqlite3.OperationalError):
            accept_proposal(flaky, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_event(self.conn, self.event_id)["registration_status"], "unknown")
        self.assertEqual(_proposal(self.conn, pid)["status"], "pending")

    def test_dismiss_rolls_back_on_failure(self):
        pid = _insert_proposal(self.conn, self.event_id, "registration_status", "unknown", "open")
        flaky = _FlakyConn(self.conn, "UPDATE event_change_proposals SET status = 'dismissed'")
        with self.assertRaises(sqlite3.OperationalError):
            dismiss_proposal(flaky, pid, resolved_at=RESOLVED_AT)
        self.assertEqual(_proposal(self.conn, pid)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
