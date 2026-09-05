"""Tests for event_dedup.find_duplicate()/match_reason(). Uses in-memory
SQLite - never touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_event_dedup -v
"""
import sqlite3
import unittest
from pathlib import Path

from event_dedup import find_duplicate, match_reason

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_event(conn, **overrides):
    fields = dict(
        name="Career Fair", source_url="https://career.eoss.asu.edu/events/foo/",
        start_date="2026-09-10", location="Tempe, ECG",
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


def _candidate(**overrides):
    base = dict(
        name="Career Fair", source_url="https://career.eoss.asu.edu/events/foo/",
        start_date="2026-09-10", location="Tempe, ECG",
    )
    base.update(overrides)
    return base


class SourceUrlTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_same_source_url_is_duplicate(self):
        event_id = _insert_event(self.conn)
        result = find_duplicate(self.conn, _candidate())
        self.assertTrue(result["is_duplicate"])
        self.assertEqual(result["existing_event_id"], event_id)
        self.assertEqual(result["reason"], "same_source_url")

    def test_equivalent_normalized_source_url_is_duplicate(self):
        _insert_event(self.conn, source_url="https://career.eoss.asu.edu/events/foo/")
        candidate = _candidate(source_url="HTTPS://Career.eoss.asu.edu/events/foo", name="Different Name")
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])
        self.assertEqual(result["reason"], "same_source_url")


class NameDateLocationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_same_name_date_location_is_duplicate(self):
        event_id = _insert_event(self.conn, source_url="https://other-source.example.com/a")
        candidate = _candidate(source_url="https://other-source.example.com/b")
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])
        self.assertEqual(result["existing_event_id"], event_id)
        self.assertEqual(result["reason"], "same_name_date_location")

    def test_same_name_different_date_is_not_duplicate(self):
        _insert_event(self.conn, source_url="https://other-source.example.com/a", start_date="2026-09-10")
        candidate = _candidate(source_url="https://other-source.example.com/b", start_date="2026-10-01")
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_similar_unrelated_names_do_not_merge(self):
        _insert_event(self.conn, name="Spring Career Fair", source_url="https://other-source.example.com/a")
        candidate = _candidate(name="Fall Career Fair", source_url="https://other-source.example.com/b")
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_different_locations_do_not_merge(self):
        _insert_event(self.conn, source_url="https://other-source.example.com/a", location="Tempe, ECG")
        candidate = _candidate(source_url="https://other-source.example.com/b", location="Scottsdale, SkySong")
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_missing_location_on_one_side_is_still_compatible(self):
        _insert_event(self.conn, source_url="https://other-source.example.com/a", location=None)
        candidate = _candidate(source_url="https://other-source.example.com/b", location="Tempe, ECG")
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])


class NoDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_no_events_no_duplicate(self):
        result = find_duplicate(self.conn, _candidate())
        self.assertEqual(result, {"is_duplicate": False, "existing_event_id": None, "reason": None})

    def test_does_not_modify_existing_event(self):
        event_id = _insert_event(self.conn)
        before = dict(self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
        find_duplicate(self.conn, _candidate())
        after = dict(self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
        self.assertEqual(before, after)


class MatchReasonUnitTests(unittest.TestCase):
    def test_match_reason_works_on_plain_dicts(self):
        existing = {"name": "Career Fair", "source_url": "https://x.example.com/a", "start_date": "2026-09-10", "location": "Tempe"}
        self.assertEqual(match_reason(_candidate(source_url="https://x.example.com/a"), existing), "same_source_url")

    def test_match_reason_none_for_unrelated(self):
        existing = {"name": "Other Event", "source_url": "https://x.example.com/z", "start_date": "2020-01-01", "location": None}
        self.assertIsNone(match_reason(_candidate(), existing))


if __name__ == "__main__":
    unittest.main()
