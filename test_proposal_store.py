"""Tests for proposal_store.store_change_proposals(). Uses in-memory SQLite
databases - never touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_proposal_store -v
"""
import sqlite3
import unittest
from pathlib import Path

from proposal_store import store_change_proposals

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


def _change(field, old_value, new_value, evidence="apply today"):
    return {"field": field, "old_value": old_value, "new_value": new_value, "evidence": evidence}


def _change_result(changes, source_result="ok"):
    return {"has_changes": bool(changes), "source_result": source_result, "changes": changes}


def _pending_rows(conn, event_id):
    return conn.execute(
        "SELECT * FROM event_change_proposals WHERE event_id = ? AND status = 'pending'",
        (event_id,),
    ).fetchall()


DETECTED_AT = "2026-09-05T00:00:00+00:00"


class BasicStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_stores_one_proposal(self):
        result = store_change_proposals(
            self.conn, self.event_id,
            _change_result([_change("registration_status", "unknown", "open")]),
            detected_at=DETECTED_AT,
        )
        self.assertEqual(len(result["inserted"]), 1)
        rows = _pending_rows(self.conn, self.event_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field"], "registration_status")
        self.assertEqual(rows[0]["old_value"], "unknown")
        self.assertEqual(rows[0]["new_value"], "open")
        self.assertEqual(rows[0]["detected_at"], DETECTED_AT)
        self.assertEqual(rows[0]["status"], "pending")

    def test_stores_multiple_proposals(self):
        result = store_change_proposals(
            self.conn, self.event_id,
            _change_result([
                _change("registration_status", "unknown", "open"),
                _change("registration_url", None, "https://example.com/apply"),
            ]),
            detected_at=DETECTED_AT,
        )
        self.assertEqual(len(result["inserted"]), 2)
        self.assertEqual(len(_pending_rows(self.conn, self.event_id)), 2)

    def test_has_changes_false_stores_nothing(self):
        result = store_change_proposals(
            self.conn, self.event_id, _change_result([]), detected_at=DETECTED_AT,
        )
        self.assertEqual(result["inserted"], [])
        self.assertEqual(_pending_rows(self.conn, self.event_id), [])

    def test_evidence_none_is_allowed(self):
        change = _change("registration_status", "unknown", "open")
        change["evidence"] = None
        store_change_proposals(
            self.conn, self.event_id, _change_result([change]), detected_at=DETECTED_AT,
        )
        row = _pending_rows(self.conn, self.event_id)[0]
        self.assertIsNone(row["evidence"])

    def test_does_not_modify_events_table(self):
        store_change_proposals(
            self.conn, self.event_id,
            _change_result([_change("registration_status", "unknown", "open")]),
            detected_at=DETECTED_AT,
        )
        event = self.conn.execute("SELECT * FROM events WHERE id = ?", (self.event_id,)).fetchone()
        self.assertEqual(event["registration_status"], "unknown")


class DuplicatePreventionTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_exact_duplicate_pending_proposal_not_duplicated(self):
        change_result = _change_result([_change("registration_status", "unknown", "open")])
        store_change_proposals(self.conn, self.event_id, change_result, detected_at=DETECTED_AT)
        result = store_change_proposals(self.conn, self.event_id, change_result, detected_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["inserted"], [])
        self.assertEqual(len(result["skipped_duplicate"]), 1)
        self.assertEqual(len(_pending_rows(self.conn, self.event_id)), 1)

    def test_accepted_previous_proposal_allows_new_pending(self):
        change_result = _change_result([_change("registration_status", "unknown", "open")])
        store_change_proposals(self.conn, self.event_id, change_result, detected_at=DETECTED_AT)
        self.conn.execute("UPDATE event_change_proposals SET status = 'accepted' WHERE event_id = ?", (self.event_id,))
        self.conn.commit()

        result = store_change_proposals(self.conn, self.event_id, change_result, detected_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(len(_pending_rows(self.conn, self.event_id)), 1)

    def test_dismissed_previous_proposal_allows_new_pending(self):
        change_result = _change_result([_change("registration_status", "unknown", "open")])
        store_change_proposals(self.conn, self.event_id, change_result, detected_at=DETECTED_AT)
        self.conn.execute("UPDATE event_change_proposals SET status = 'dismissed' WHERE event_id = ?", (self.event_id,))
        self.conn.commit()

        result = store_change_proposals(self.conn, self.event_id, change_result, detected_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(len(result["inserted"]), 1)
        self.assertEqual(len(_pending_rows(self.conn, self.event_id)), 1)

    def test_different_new_value_creates_separate_proposal(self):
        store_change_proposals(
            self.conn, self.event_id,
            _change_result([_change("registration_status", "unknown", "not_open")]),
            detected_at=DETECTED_AT,
        )
        result = store_change_proposals(
            self.conn, self.event_id,
            _change_result([_change("registration_status", "unknown", "open")]),
            detected_at="2026-09-06T00:00:00+00:00",
        )
        self.assertEqual(len(result["inserted"]), 1)
        rows = _pending_rows(self.conn, self.event_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["new_value"] for r in rows}, {"not_open", "open"})


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.event_id = _insert_event(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_unsupported_field_raises_value_error(self):
        with self.assertRaises(ValueError):
            store_change_proposals(
                self.conn, self.event_id,
                _change_result([_change("name", "Old Name", "New Name")]),
                detected_at=DETECTED_AT,
            )

    def test_missing_new_value_raises_value_error(self):
        change = {"field": "registration_status", "old_value": "unknown"}
        with self.assertRaises(ValueError):
            store_change_proposals(self.conn, self.event_id, _change_result([change]), detected_at=DETECTED_AT)

    def test_empty_new_value_raises_value_error(self):
        change = _change("registration_status", "unknown", "")
        with self.assertRaises(ValueError):
            store_change_proposals(self.conn, self.event_id, _change_result([change]), detected_at=DETECTED_AT)

    def test_invalid_change_structure_raises_value_error(self):
        bad_result = {"has_changes": True, "source_result": "ok", "changes": "not-a-list"}
        with self.assertRaises(ValueError):
            store_change_proposals(self.conn, self.event_id, bad_result, detected_at=DETECTED_AT)

    def test_event_id_must_exist(self):
        with self.assertRaises(LookupError):
            store_change_proposals(
                self.conn, 99999,
                _change_result([_change("registration_status", "unknown", "open")]),
                detected_at=DETECTED_AT,
            )

    def test_transaction_rolls_back_if_one_change_invalid(self):
        change_result = _change_result([
            _change("registration_status", "unknown", "open"),
            _change("not_a_real_field", "x", "y"),
        ])
        with self.assertRaises(ValueError):
            store_change_proposals(self.conn, self.event_id, change_result, detected_at=DETECTED_AT)
        self.assertEqual(_pending_rows(self.conn, self.event_id), [])


if __name__ == "__main__":
    unittest.main()
