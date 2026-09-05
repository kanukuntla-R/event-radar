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


class CrossSourceDedupTests(unittest.TestCase):
    """V0.8B: the fallback match must work across two different sources
    with two different URLs for the same real-world event."""

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_same_event_different_source_and_url_is_duplicate(self):
        event_id = _insert_event(
            self.conn, name="Fall 2026 Career & Internship Fair",
            source="ASU Career Services", source_url="https://career.eoss.asu.edu/events/fair/",
            start_date="2026-10-01", location="Tempe, ECG",
        )
        candidate = _candidate(
            name="Fall 2026 Career and Internship Fair", source="ASU Events",
            source_url="https://asuevents.asu.edu/event/fall-career-fair",
            start_date="2026-10-01", location="Tempe, ECG",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])
        self.assertEqual(result["existing_event_id"], event_id)
        self.assertEqual(result["existing_source"], "ASU Career Services")
        self.assertEqual(result["reason"], "same_name_date_location")

    def test_ampersand_vs_and_normalized(self):
        self.assertEqual(
            match_reason(
                _candidate(name="Career & Internship Fair"),
                {"name": "Career and Internship Fair", "source_url": "https://x.example.com/a",
                 "start_date": "2026-09-10", "location": "Tempe"},
            ),
            "same_name_date_location",
        )

    def test_punctuation_and_case_differences_normalized(self):
        self.assertEqual(
            match_reason(
                _candidate(name="  CAREER FAIR!!  "),
                {"name": "career fair", "source_url": "https://x.example.com/a",
                 "start_date": "2026-09-10", "location": "Tempe"},
            ),
            "same_name_date_location",
        )

    def test_same_title_different_date_is_not_duplicate(self):
        _insert_event(self.conn, name="Career Fair", start_date="2026-09-10")
        candidate = _candidate(name="Career Fair", start_date="2026-11-01", source_url="https://other.example.com/x")
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_same_date_incompatible_location_is_not_duplicate(self):
        _insert_event(self.conn, name="Career Fair", start_date="2026-09-10", location="Tempe campus")
        candidate = _candidate(
            name="Career Fair", start_date="2026-09-10", location="Downtown Phoenix campus",
            source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_incompatible_start_time_is_not_duplicate(self):
        _insert_event(self.conn, name="Office Hours", start_date="2026-09-10", start_time="10:00")
        candidate = _candidate(
            name="Office Hours", start_date="2026-09-10", start_time="16:00",
            source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_missing_start_time_on_one_side_is_still_compatible(self):
        _insert_event(self.conn, name="Office Hours", start_date="2026-09-10", start_time=None)
        candidate = _candidate(
            name="Office Hours", start_date="2026-09-10", start_time="10:00",
            source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])

    def test_similar_generic_titles_do_not_merge(self):
        _insert_event(self.conn, name="Career Quick Takes: Career Exploration", start_date="2026-09-08")
        candidate = _candidate(
            name="Career Quick Takes: Job or Internship Search", start_date="2026-09-08",
            source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_existing_row_never_modified(self):
        event_id = _insert_event(self.conn, name="Career Fair", start_date="2026-09-10")
        before = dict(self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
        find_duplicate(self.conn, _candidate(name="Career Fair", start_date="2026-09-10", source_url="https://other.example.com/x"))
        after = dict(self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
        self.assertEqual(before, after)


class UnknownLocationCompatibilityTests(unittest.TestCase):
    """'other' means unmapped/unknown, not a shared physical place - two
    different unrecognized buildings must never match just because neither
    was recognized."""

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_different_raw_locations_both_area_other_is_not_duplicate(self):
        _insert_event(
            self.conn, name="Research Symposium", start_date="2026-09-20",
            start_time="10:00", location="Biodesign Auditorium",
        )
        candidate = _candidate(
            name="Research Symposium", start_date="2026-09-20", start_time="10:00",
            location="Wexler Hall", source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_identical_raw_unknown_location_is_duplicate(self):
        event_id = _insert_event(
            self.conn, name="Research Symposium", start_date="2026-09-20",
            start_time="10:00", location="Wexler Hall",
        )
        candidate = _candidate(
            name="Research Symposium", start_date="2026-09-20", start_time="10:00",
            location="Wexler Hall", source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])
        self.assertEqual(result["existing_event_id"], event_id)

    def test_tempe_text_variants_are_duplicate_via_meaningful_area(self):
        _insert_event(self.conn, name="Career Fair", start_date="2026-09-10", location="Tempe, ECG")
        candidate = _candidate(
            name="Career Fair", start_date="2026-09-10", location="Tempe campus",
            source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])

    def test_zoom_vs_online_event_is_duplicate_via_online_area(self):
        _insert_event(self.conn, name="Office Hours", start_date="2026-09-08", location="Zoom")
        candidate = _candidate(
            name="Office Hours", start_date="2026-09-08", location="Online event",
            source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])

    def test_tempe_vs_phoenix_is_not_duplicate(self):
        _insert_event(self.conn, name="Career Fair", start_date="2026-09-10", location="Tempe campus")
        candidate = _candidate(
            name="Career Fair", start_date="2026-09-10", location="Downtown Phoenix campus",
            source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertFalse(result["is_duplicate"])

    def test_one_side_missing_location_still_compatible(self):
        _insert_event(self.conn, name="Career Fair", start_date="2026-09-10", location=None)
        candidate = _candidate(
            name="Career Fair", start_date="2026-09-10", location="Wexler Hall",
            source_url="https://other.example.com/x",
        )
        result = find_duplicate(self.conn, candidate)
        self.assertTrue(result["is_duplicate"])

    def test_existing_row_never_modified_for_other_area_case(self):
        event_id = _insert_event(self.conn, name="Research Symposium", start_date="2026-09-20", location="Biodesign Auditorium")
        before = dict(self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
        find_duplicate(self.conn, _candidate(
            name="Research Symposium", start_date="2026-09-20", location="Wexler Hall",
            source_url="https://other.example.com/x",
        ))
        after = dict(self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
