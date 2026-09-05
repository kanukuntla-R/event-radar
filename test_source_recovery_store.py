"""Tests for source_recovery_store.store_recovery_candidates(). Uses
in-memory SQLite databases - never touches data/events.db. Stdlib unittest
only.

Run with: python -m unittest test_source_recovery_store -v
"""
import sqlite3
import unittest
from pathlib import Path

from source_recovery_store import store_recovery_candidates

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
DISCOVERED_AT = "2026-09-05T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_event(conn, **overrides):
    fields = dict(name="Test Hack", source_url="https://old-site.example.com", consecutive_failures=3)
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


def _candidate(url, candidate_type="mlh", confidence="medium", title=None, evidence=None):
    return {
        "candidate_url": url, "candidate_type": candidate_type,
        "confidence": confidence, "title": title, "evidence": evidence,
    }


def _pending_rows(conn, event_id):
    return conn.execute(
        "SELECT * FROM source_recovery_candidates WHERE event_id = ? AND status = 'pending'",
        (event_id,),
    ).fetchall()


class BasicStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_stores_one_candidate(self):
        result = store_recovery_candidates(
            self.conn, self.event_id, [_candidate("https://mlh.io/events/test")], discovered_at=DISCOVERED_AT
        )
        self.assertEqual(len(result["inserted"]), 1)
        rows = _pending_rows(self.conn, self.event_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_url"], "https://mlh.io/events/test")
        self.assertEqual(rows[0]["discovered_at"], DISCOVERED_AT)
        self.assertEqual(rows[0]["status"], "pending")

    def test_stores_multiple_candidates(self):
        result = store_recovery_candidates(
            self.conn, self.event_id,
            [_candidate("https://mlh.io/events/test"), _candidate("https://umich.edu/test", "official_university", "high")],
            discovered_at=DISCOVERED_AT,
        )
        self.assertEqual(len(result["inserted"]), 2)
        self.assertEqual(len(_pending_rows(self.conn, self.event_id)), 2)

    def test_empty_candidates_stores_nothing(self):
        result = store_recovery_candidates(self.conn, self.event_id, [], discovered_at=DISCOVERED_AT)
        self.assertEqual(result["inserted"], [])
        self.assertEqual(_pending_rows(self.conn, self.event_id), [])

    def test_does_not_modify_events_table(self):
        store_recovery_candidates(
            self.conn, self.event_id, [_candidate("https://mlh.io/events/test")], discovered_at=DISCOVERED_AT
        )
        event = self.conn.execute("SELECT * FROM events WHERE id = ?", (self.event_id,)).fetchone()
        self.assertEqual(event["source_url"], "https://old-site.example.com")


class DuplicatePreventionTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_exact_duplicate_pending_not_duplicated(self):
        candidates = [_candidate("https://mlh.io/events/test")]
        store_recovery_candidates(self.conn, self.event_id, candidates, discovered_at=DISCOVERED_AT)
        result = store_recovery_candidates(self.conn, self.event_id, candidates, discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["inserted"], [])
        self.assertEqual(len(result["skipped_duplicate"]), 1)
        self.assertEqual(len(_pending_rows(self.conn, self.event_id)), 1)

    def test_accepted_previous_allows_new_pending(self):
        candidates = [_candidate("https://mlh.io/events/test")]
        store_recovery_candidates(self.conn, self.event_id, candidates, discovered_at=DISCOVERED_AT)
        self.conn.execute("UPDATE source_recovery_candidates SET status = 'accepted' WHERE event_id = ?", (self.event_id,))
        self.conn.commit()

        result = store_recovery_candidates(self.conn, self.event_id, candidates, discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(len(_pending_rows(self.conn, self.event_id)), 1)

    def test_dismissed_previous_allows_new_pending(self):
        candidates = [_candidate("https://mlh.io/events/test")]
        store_recovery_candidates(self.conn, self.event_id, candidates, discovered_at=DISCOVERED_AT)
        self.conn.execute("UPDATE source_recovery_candidates SET status = 'dismissed' WHERE event_id = ?", (self.event_id,))
        self.conn.commit()

        result = store_recovery_candidates(self.conn, self.event_id, candidates, discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(len(result["inserted"]), 1)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_missing_candidate_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            store_recovery_candidates(self.conn, self.event_id, [_candidate(None)], discovered_at=DISCOVERED_AT)

    def test_unsupported_candidate_type_raises_value_error(self):
        with self.assertRaises(ValueError):
            store_recovery_candidates(
                self.conn, self.event_id, [_candidate("https://x.com/a", candidate_type="bogus")],
                discovered_at=DISCOVERED_AT,
            )

    def test_unsupported_confidence_raises_value_error(self):
        with self.assertRaises(ValueError):
            store_recovery_candidates(
                self.conn, self.event_id, [_candidate("https://x.com/a", confidence="certain")],
                discovered_at=DISCOVERED_AT,
            )

    def test_event_id_must_exist(self):
        with self.assertRaises(LookupError):
            store_recovery_candidates(
                self.conn, 99999, [_candidate("https://mlh.io/events/test")], discovered_at=DISCOVERED_AT
            )

    def test_transaction_rolls_back_if_one_candidate_invalid(self):
        candidates = [_candidate("https://mlh.io/events/test"), _candidate("https://x.com/a", candidate_type="bogus")]
        with self.assertRaises(ValueError):
            store_recovery_candidates(self.conn, self.event_id, candidates, discovered_at=DISCOVERED_AT)
        self.assertEqual(_pending_rows(self.conn, self.event_id), [])


if __name__ == "__main__":
    unittest.main()
