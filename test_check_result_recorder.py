"""Tests for check_result_recorder.record_check_result(). Uses in-memory
SQLite databases - never touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_check_result_recorder -v
"""
import sqlite3
import unittest
from pathlib import Path

from check_result_recorder import record_check_result

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()


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


def _collector_result(source_result="ok", checked_at="2026-09-05T00:00:00+00:00"):
    return {
        "source_result": source_result,
        "requested_url": "https://example.com",
        "final_url": "https://example.com",
        "http_status": 200,
        "checked_at": checked_at,
        "page_title": "Example",
        "raw_evidence": {},
        "detected": {
            "start_date": None, "end_date": None, "date_status": None,
            "registration_status": "open", "registration_url": "https://example.com/apply",
            "application_deadline": None,
        },
        "notes": [],
    }


def _fetch(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


class SuccessTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_first_successful_check(self):
        record_check_result(self.conn, self.event_id, _collector_result("ok", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["last_checked"], "2026-09-05T00:00:00+00:00")
        self.assertEqual(row["last_check_status"], "ok")
        self.assertEqual(row["last_successful_check"], "2026-09-05T00:00:00+00:00")
        self.assertEqual(row["consecutive_failures"], 0)

    def test_redirected_counts_as_success(self):
        record_check_result(self.conn, self.event_id, _collector_result("redirected", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["last_check_status"], "redirected")
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["last_successful_check"], "2026-09-05T00:00:00+00:00")

    def test_success_resets_previous_failures_to_zero(self):
        self.conn.execute("UPDATE events SET consecutive_failures = 2 WHERE id = ?", (self.event_id,))
        self.conn.commit()
        record_check_result(self.conn, self.event_id, _collector_result("ok", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["consecutive_failures"], 0)

    def test_success_updates_last_successful_check(self):
        record_check_result(self.conn, self.event_id, _collector_result("ok", "2026-08-01T00:00:00+00:00"))
        record_check_result(self.conn, self.event_id, _collector_result("ok", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["last_successful_check"], "2026-09-05T00:00:00+00:00")


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_first_failure_increments_zero_to_one(self):
        record_check_result(self.conn, self.event_id, _collector_result("not_found", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["consecutive_failures"], 1)
        self.assertEqual(row["last_check_status"], "not_found")
        self.assertEqual(row["event_state"], "active")

    def test_second_failure_increments_one_to_two(self):
        record_check_result(self.conn, self.event_id, _collector_result("unavailable", "2026-09-01T00:00:00+00:00"))
        record_check_result(self.conn, self.event_id, _collector_result("unavailable", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["consecutive_failures"], 2)
        self.assertEqual(row["event_state"], "active")

    def test_third_failure_increments_two_to_three_event_state_stays_active(self):
        for i in range(3):
            record_check_result(
                self.conn, self.event_id,
                _collector_result("parse_failed", f"2026-09-0{i + 1}T00:00:00+00:00"),
            )
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["consecutive_failures"], 3)
        self.assertEqual(row["event_state"], "active")
        self.assertIsNone(row["inactive_reason"])

    def test_failure_preserves_previous_last_successful_check(self):
        record_check_result(self.conn, self.event_id, _collector_result("ok", "2026-08-01T00:00:00+00:00"))
        record_check_result(self.conn, self.event_id, _collector_result("not_found", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["last_successful_check"], "2026-08-01T00:00:00+00:00")
        self.assertEqual(row["last_checked"], "2026-09-05T00:00:00+00:00")
        self.assertEqual(row["consecutive_failures"], 1)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_unknown_source_result_raises_value_error_with_no_write(self):
        with self.assertRaises(ValueError):
            record_check_result(self.conn, self.event_id, _collector_result("bogus", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertIsNone(row["last_checked"])

    def test_missing_checked_at_raises_value_error_with_no_write(self):
        result = _collector_result("ok", checked_at=None)
        with self.assertRaises(ValueError):
            record_check_result(self.conn, self.event_id, result)
        row = _fetch(self.conn, self.event_id)
        self.assertIsNone(row["last_checked"])

    def test_invalid_checked_at_raises_value_error_with_no_write(self):
        result = _collector_result("ok", checked_at="not-a-date")
        with self.assertRaises(ValueError):
            record_check_result(self.conn, self.event_id, result)
        row = _fetch(self.conn, self.event_id)
        self.assertIsNone(row["last_checked"])

    def test_nonexistent_event_id_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            record_check_result(self.conn, 99999, _collector_result("ok", "2026-09-05T00:00:00+00:00"))


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_does_not_modify_event_fact_fields(self):
        # collector_result detects registration_status=open / a registration_url,
        # neither must be written by the recorder.
        record_check_result(self.conn, self.event_id, _collector_result("ok", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["registration_status"], "unknown")
        self.assertIsNone(row["registration_url"])
        self.assertEqual(row["start_date"], "2026-10-03")
        self.assertEqual(row["end_date"], "2026-10-04")
        self.assertEqual(row["date_status"], "confirmed")

    def test_does_not_modify_status(self):
        self.conn.execute("UPDATE events SET status = 'watching' WHERE id = ?", (self.event_id,))
        self.conn.commit()
        record_check_result(self.conn, self.event_id, _collector_result("ok", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["status"], "watching")

    def test_does_not_modify_event_state_or_inactive_reason(self):
        record_check_result(self.conn, self.event_id, _collector_result("not_found", "2026-09-05T00:00:00+00:00"))
        row = _fetch(self.conn, self.event_id)
        self.assertEqual(row["event_state"], "active")
        self.assertIsNone(row["inactive_reason"])


if __name__ == "__main__":
    unittest.main()
