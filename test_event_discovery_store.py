"""Tests for event_discovery_store.store_discovered_events(). Uses
in-memory SQLite - never touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_event_discovery_store -v
"""
import sqlite3
import unittest
from pathlib import Path

from event_discovery_store import store_discovered_events, validate_candidate

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
DISCOVERED_AT = "2026-09-05T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _candidate(**overrides):
    base = dict(
        name="FSE Workshop: Creating Your Technical Resume",
        description="Learn how to write a resume.",
        start_date="2026-09-08", end_date="2026-09-08",
        start_time="10:00", end_time="11:00", timezone="America/Phoenix",
        date_status="confirmed", location="https://asu.zoom.us/j/1", location_type="online",
        location_area="online", event_subtype="workshop", source="ASU Career Services",
        source_url="https://career.eoss.asu.edu/events/foo/", organizer="ASU Career Services",
        registration_status="unknown", registration_url=None, application_deadline=None,
    )
    base.update(overrides)
    return base


def _insert_hackathon(conn, **overrides):
    fields = dict(name="MHacks", event_type="hackathon", source_url="https://mhacks.org", status="watching")
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()


class BasicStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_new_candidate_inserted(self):
        result = store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(len(result["event_ids"]), 1)

    def test_status_defaults_new_and_event_type_asu_and_active(self):
        result = store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (result["event_ids"][0],)).fetchone()
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["event_type"], "asu")
        self.assertEqual(row["event_state"], "active")
        self.assertEqual(row["first_seen"], DISCOVERED_AT)

    def test_all_normalized_fields_stored(self):
        result = store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (result["event_ids"][0],)).fetchone()
        self.assertEqual(row["location_area"], "online")
        self.assertEqual(row["event_subtype"], "workshop")
        self.assertEqual(row["timezone"], "America/Phoenix")

    def test_hackathon_unaffected(self):
        _insert_hackathon(self.conn)
        store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        row = self.conn.execute("SELECT * FROM events WHERE event_type = 'hackathon'").fetchone()
        self.assertEqual(row["name"], "MHacks")
        self.assertEqual(row["status"], "watching")


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_duplicate_skipped(self):
        store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        result = store_discovered_events(self.conn, [_candidate()], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["inserted"], [])
        self.assertEqual(len(result["duplicates_skipped"]), 1)

    def test_existing_row_unchanged_after_duplicate_detected(self):
        first = store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        before = dict(self.conn.execute("SELECT * FROM events WHERE id = ?", (first["event_ids"][0],)).fetchone())
        store_discovered_events(self.conn, [_candidate(name="Renamed?")], discovered_at="2026-09-06T00:00:00+00:00")
        after = dict(self.conn.execute("SELECT * FROM events WHERE id = ?", (first["event_ids"][0],)).fetchone())
        self.assertEqual(before, after)

    def test_duplicate_within_same_batch_is_caught(self):
        result = store_discovered_events(
            self.conn, [_candidate(), _candidate()], discovered_at=DISCOVERED_AT
        )
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(len(result["duplicates_skipped"]), 1)


class InvalidCandidateTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_missing_name_is_invalid(self):
        result = store_discovered_events(self.conn, [_candidate(name=None)], discovered_at=DISCOVERED_AT)
        self.assertEqual(result["inserted"], [])
        self.assertEqual(len(result["invalid_skipped"]), 1)
        self.assertIn("name", result["invalid_skipped"][0]["reason"])

    def test_missing_source_url_is_invalid(self):
        result = store_discovered_events(self.conn, [_candidate(source_url=None)], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["invalid_skipped"]), 1)

    def test_missing_start_date_is_invalid(self):
        result = store_discovered_events(self.conn, [_candidate(start_date=None)], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["invalid_skipped"]), 1)

    def test_validate_candidate_pure_function(self):
        self.assertEqual(validate_candidate(_candidate()), (True, None))
        is_valid, reason = validate_candidate(_candidate(name=""))
        self.assertFalse(is_valid)
        self.assertIn("name", reason)

    def test_invalid_does_not_block_other_candidates(self):
        result = store_discovered_events(
            self.conn, [_candidate(name=None), _candidate(source_url="https://career.eoss.asu.edu/events/bar/")],
            discovered_at=DISCOVERED_AT,
        )
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(len(result["invalid_skipped"]), 1)


class TimedEventSafetyTests(unittest.TestCase):
    """Regression: start_time=None must only be storable when the candidate
    explicitly confirms an all-day event - otherwise it's an incomplete
    time, not a fabricated all-day one."""

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_normal_timed_candidate_stores_normally(self):
        result = store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["inserted"]), 1)
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (result["event_ids"][0],)).fetchone()
        self.assertEqual(row["start_time"], "10:00")

    def test_explicit_all_day_candidate_may_store_with_null_start_time(self):
        candidate = _candidate(start_time=None, end_time=None, timezone=None, all_day=True)
        result = store_discovered_events(self.conn, [candidate], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["inserted"]), 1)
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (result["event_ids"][0],)).fetchone()
        self.assertIsNone(row["start_time"])

    def test_timeless_candidate_without_all_day_flag_is_invalid(self):
        candidate = _candidate(start_time=None, end_time=None, timezone=None)
        result = store_discovered_events(self.conn, [candidate], discovered_at=DISCOVERED_AT)
        self.assertEqual(result["inserted"], [])
        self.assertEqual(len(result["invalid_skipped"]), 1)
        self.assertIn("time", result["invalid_skipped"][0]["reason"])

    def test_timeless_candidate_with_all_day_false_is_invalid(self):
        candidate = _candidate(start_time=None, end_time=None, timezone=None, all_day=False)
        result = store_discovered_events(self.conn, [candidate], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["invalid_skipped"]), 1)

    def test_rejected_incomplete_candidate_produces_zero_db_writes(self):
        candidate = _candidate(start_time=None, end_time=None, timezone=None)
        store_discovered_events(self.conn, [candidate], discovered_at=DISCOVERED_AT)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_bad_time_candidate_does_not_block_valid_candidates_in_same_batch(self):
        bad = _candidate(name="Bad Time Event", source_url="https://career.eoss.asu.edu/events/bad/", start_time=None, end_time=None, timezone=None)
        good = _candidate(name="Good Time Event", source_url="https://career.eoss.asu.edu/events/good/")
        result = store_discovered_events(self.conn, [bad, good], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(result["inserted"][0]["name"], "Good Time Event")
        self.assertEqual(len(result["invalid_skipped"]), 1)
        self.assertEqual(result["invalid_skipped"][0]["name"], "Bad Time Event")

    def test_dry_run_reports_incomplete_time_candidate_clearly(self):
        candidate = _candidate(start_time=None, end_time=None, timezone=None)
        result = store_discovered_events(self.conn, [candidate], discovered_at=DISCOVERED_AT, dry_run=True)
        self.assertEqual(result["inserted"], [])
        self.assertEqual(len(result["invalid_skipped"]), 1)
        self.assertIn("time", result["invalid_skipped"][0]["reason"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_dry_run_makes_zero_writes(self):
        result = store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT, dry_run=True)
        self.assertEqual(len(result["inserted"]), 1)
        self.assertIsNone(result["inserted"][0]["event_id"])
        self.assertEqual(result["event_ids"], [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_dry_run_still_reports_duplicates_against_real_db(self):
        store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        result = store_discovered_events(self.conn, [_candidate()], discovered_at=DISCOVERED_AT, dry_run=True)
        self.assertEqual(result["inserted"], [])
        self.assertEqual(len(result["duplicates_skipped"]), 1)


if __name__ == "__main__":
    unittest.main()
