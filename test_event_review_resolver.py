"""Tests for event_review_resolver.accept_review_candidate()/
dismiss_review_candidate(). Uses in-memory SQLite - never touches
data/events.db. Stdlib unittest only.

Run with: python -m unittest test_event_review_resolver -v
"""
import json
import sqlite3
import unittest
from pathlib import Path

from event_review_resolver import accept_review_candidate, dismiss_review_candidate

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
RESOLVED_AT = "2026-09-06T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_review(conn, status="pending", **overrides):
    fields = dict(
        name="Maosho", event_type="asu", event_subtype="other",
        description="An academic talk.", start_date="2026-09-10", end_date="2026-09-10",
        start_time="17:00", end_time="18:00", timezone="America/Phoenix",
        location="MU Alumni Lounge", location_type="in_person", location_area="tempe",
        source="ASU Events", source_url="https://asuevents.asu.edu/event/maosho",
        organizer="ASU Events", registration_status="unknown", registration_url=None,
        application_deadline=None, review_reason="no clear evidence",
        review_signals=json.dumps(["supporting_category:academic events"]),
        discovered_at="2026-09-05T00:00:00+00:00", status=status, resolved_at=None,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO event_review_candidates ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


def _insert_event(conn, **overrides):
    fields = dict(name="Existing Event", source="ASU Events", source_url="https://asuevents.asu.edu/event/other")
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()


def _review(conn, review_id):
    return conn.execute("SELECT * FROM event_review_candidates WHERE id = ?", (review_id,)).fetchone()


def _event(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


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


class AllDayAcceptTests(unittest.TestCase):
    """V0.9B.2: accepting a review candidate must use the persisted
    all_day column, not an inference from start_time being NULL."""

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_accepting_explicit_all_day_review_creates_valid_all_day_event(self):
        rid = _insert_review(self.conn, start_time=None, end_time=None, timezone=None, all_day=1)
        outcome = accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        self.assertIsNotNone(outcome["event_id"])
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (outcome["event_id"],)).fetchone()
        self.assertIsNone(row["start_time"])
        self.assertEqual(row["start_date"], "2026-09-10")

    def test_timed_review_candidate_unaffected_by_all_day_column(self):
        rid = _insert_review(self.conn, all_day=0)
        outcome = accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (outcome["event_id"],)).fetchone()
        self.assertEqual(row["start_time"], "17:00")

    def test_missing_time_and_all_day_0_still_fails_safely(self):
        rid = _insert_review(self.conn, start_time=None, end_time=None, timezone=None, all_day=0)
        with self.assertRaises(ValueError):
            accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


class AcceptTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_accept_inserts_event_as_new_and_active(self):
        rid = _insert_review(self.conn)
        outcome = accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        self.assertIsNotNone(outcome["event_id"])
        row = _event(self.conn, outcome["event_id"])
        self.assertEqual(row["name"], "Maosho")
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["event_state"], "active")

    def test_accept_marks_review_candidate_accepted(self):
        rid = _insert_review(self.conn)
        accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        row = _review(self.conn, rid)
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["resolved_at"], RESOLVED_AT)

    def test_accept_preserves_source_asu_events(self):
        rid = _insert_review(self.conn)
        outcome = accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        row = _event(self.conn, outcome["event_id"])
        self.assertEqual(row["source"], "ASU Events")

    def test_accept_does_not_make_interested(self):
        rid = _insert_review(self.conn)
        outcome = accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        row = _event(self.conn, outcome["event_id"])
        self.assertNotEqual(row["status"], "interested")

    def test_accept_duplicate_does_not_insert_second_event(self):
        _insert_event(self.conn, name="Maosho", source_url="https://asuevents.asu.edu/event/maosho", start_date="2026-09-10")
        rid = _insert_review(self.conn)
        outcome = accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        self.assertTrue(outcome["duplicate"])
        self.assertIsNone(outcome["event_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(_review(self.conn, rid)["status"], "accepted")

    def test_invalid_candidate_does_not_silently_insert(self):
        # a dateless candidate is legitimately storable in the review queue
        # (event_review_store doesn't require a date), but events.
        # validate_candidate DOES require one - accept must refuse, not
        # silently insert a dateless event.
        rid = _insert_review(self.conn, start_date=None, end_date=None, start_time=None, end_time=None, timezone=None)
        with self.assertRaises(ValueError):
            accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(_review(self.conn, rid)["status"], "pending")

    def test_cannot_accept_already_resolved(self):
        rid = _insert_review(self.conn, status="accepted")
        with self.assertRaises(ValueError):
            accept_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)

    def test_missing_candidate_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            accept_review_candidate(self.conn, 99999, resolved_at=RESOLVED_AT)

    def test_accept_rolls_back_if_review_update_fails(self):
        rid = _insert_review(self.conn)
        flaky = _FlakyConn(self.conn, "UPDATE event_review_candidates SET status = 'accepted'")
        with self.assertRaises(sqlite3.OperationalError):
            accept_review_candidate(flaky, rid, resolved_at=RESOLVED_AT)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(_review(self.conn, rid)["status"], "pending")


class DismissTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_dismiss_changes_only_review_candidate(self):
        rid = _insert_review(self.conn)
        dismiss_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        row = _review(self.conn, rid)
        self.assertEqual(row["status"], "dismissed")
        self.assertEqual(row["resolved_at"], RESOLVED_AT)

    def test_dismiss_does_not_insert_event(self):
        rid = _insert_review(self.conn)
        dismiss_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_cannot_dismiss_already_resolved(self):
        rid = _insert_review(self.conn, status="dismissed")
        with self.assertRaises(ValueError):
            dismiss_review_candidate(self.conn, rid, resolved_at=RESOLVED_AT)

    def test_missing_candidate_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            dismiss_review_candidate(self.conn, 99999, resolved_at=RESOLVED_AT)

    def test_dismiss_rolls_back_on_failure(self):
        rid = _insert_review(self.conn)
        flaky = _FlakyConn(self.conn, "UPDATE event_review_candidates SET status = 'dismissed'")
        with self.assertRaises(sqlite3.OperationalError):
            dismiss_review_candidate(flaky, rid, resolved_at=RESOLVED_AT)
        self.assertEqual(_review(self.conn, rid)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
