"""Tests for event_review_store.store_review_candidates(). Uses in-memory
SQLite - never touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_event_review_store -v
"""
import sqlite3
import unittest
from pathlib import Path

from event_review_store import store_review_candidates, validate_review_candidate

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
DISCOVERED_AT = "2026-09-05T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _candidate(**overrides):
    base = dict(
        name="Maosho", description="An academic talk.", start_date="2026-09-10",
        end_date="2026-09-10", start_time="17:00", end_time="18:00", timezone="America/Phoenix",
        location="MU Alumni Lounge", location_type="in_person", location_area="tempe",
        event_subtype="other", source="ASU Events",
        source_url="https://asuevents.asu.edu/event/maosho", organizer="ASU Events",
        registration_status="unknown", registration_url=None, application_deadline=None,
        review_reason="no clear include or exclude evidence",
        review_signals=["supporting_category:academic events"],
    )
    base.update(overrides)
    return base


def _pending_rows(conn):
    return conn.execute("SELECT * FROM event_review_candidates WHERE status = 'pending'").fetchall()


class BasicStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_review_candidate_stored(self):
        result = store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["stored"]), 1)
        rows = _pending_rows(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Maosho")
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(rows[0]["discovered_at"], DISCOVERED_AT)

    def test_review_reason_and_signals_stored(self):
        store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        row = _pending_rows(self.conn)[0]
        self.assertEqual(row["review_reason"], "no clear include or exclude evidence")
        self.assertIn("academic events", row["review_signals"])

    def test_empty_candidates_stores_nothing(self):
        result = store_review_candidates(self.conn, [], discovered_at=DISCOVERED_AT)
        self.assertEqual(result["stored"], [])
        self.assertEqual(_pending_rows(self.conn), [])


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_repeated_discovery_does_not_duplicate_pending(self):
        store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        result = store_review_candidates(self.conn, [_candidate()], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["stored"], [])
        self.assertEqual(len(result["existing"]), 1)
        self.assertEqual(len(_pending_rows(self.conn)), 1)

    def test_accepted_review_row_does_not_get_recreated(self):
        store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        self.conn.execute("UPDATE event_review_candidates SET status = 'accepted'")
        self.conn.commit()
        result = store_review_candidates(self.conn, [_candidate()], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["stored"], [])
        self.assertEqual(result["existing"][0]["status"], "accepted")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_review_candidates").fetchone()[0], 1)

    def test_dismissed_review_row_does_not_get_recreated(self):
        store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        self.conn.execute("UPDATE event_review_candidates SET status = 'dismissed'")
        self.conn.commit()
        result = store_review_candidates(self.conn, [_candidate()], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["stored"], [])
        self.assertEqual(result["existing"][0]["status"], "dismissed")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_review_candidates").fetchone()[0], 1)

    def test_materially_different_event_still_creates_new_review(self):
        store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        self.conn.execute("UPDATE event_review_candidates SET status = 'dismissed'")
        self.conn.commit()
        different = _candidate(start_date="2026-11-01", source_url="https://asuevents.asu.edu/event/maosho-2")
        result = store_review_candidates(self.conn, [different], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(len(result["stored"]), 1)
        self.assertEqual(len(_pending_rows(self.conn)), 1)


class SameUrlResolvedRowMaterialChangeTests(unittest.TestCase):
    """Regression: a resolved (accepted/dismissed) row must not suppress a
    materially different candidate forever just because the URL is
    unchanged - the human decision applied to that event snapshot, not to
    everything that URL might ever host."""

    SAME_URL = "https://asuevents.asu.edu/event/123"

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_pending_same_url_unchanged_is_existing(self):
        store_review_candidates(self.conn, [_candidate(source_url=self.SAME_URL)], discovered_at=DISCOVERED_AT)
        result = store_review_candidates(self.conn, [_candidate(source_url=self.SAME_URL)], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["stored"], [])
        self.assertEqual(result["existing"][0]["status"], "pending")

    def test_pending_same_url_changed_date_is_still_existing_pending(self):
        store_review_candidates(self.conn, [_candidate(source_url=self.SAME_URL, start_date="2026-09-20")], discovered_at=DISCOVERED_AT)
        changed = _candidate(source_url=self.SAME_URL, start_date="2026-10-15")
        result = store_review_candidates(self.conn, [changed], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["stored"], [])
        self.assertEqual(result["existing"][0]["status"], "pending")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_review_candidates").fetchone()[0], 1)

    def test_dismissed_same_url_unchanged_is_existing(self):
        store_review_candidates(self.conn, [_candidate(source_url=self.SAME_URL)], discovered_at=DISCOVERED_AT)
        self.conn.execute("UPDATE event_review_candidates SET status = 'dismissed'")
        self.conn.commit()
        result = store_review_candidates(self.conn, [_candidate(source_url=self.SAME_URL)], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["stored"], [])
        self.assertEqual(result["existing"][0]["status"], "dismissed")

    def test_accepted_same_url_unchanged_is_existing(self):
        store_review_candidates(self.conn, [_candidate(source_url=self.SAME_URL)], discovered_at=DISCOVERED_AT)
        self.conn.execute("UPDATE event_review_candidates SET status = 'accepted'")
        self.conn.commit()
        result = store_review_candidates(self.conn, [_candidate(source_url=self.SAME_URL)], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["stored"], [])
        self.assertEqual(result["existing"][0]["status"], "accepted")

    def test_dismissed_same_url_changed_date_creates_fresh_pending(self):
        store_review_candidates(
            self.conn, [_candidate(source_url=self.SAME_URL, start_date="2026-09-20", end_date="2026-09-20")],
            discovered_at=DISCOVERED_AT,
        )
        self.conn.execute("UPDATE event_review_candidates SET status = 'dismissed'")
        self.conn.commit()
        changed = _candidate(source_url=self.SAME_URL, start_date="2026-10-15", end_date="2026-10-15")
        result = store_review_candidates(self.conn, [changed], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(len(result["stored"]), 1)
        self.assertEqual(len(_pending_rows(self.conn)), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_review_candidates").fetchone()[0], 2)

    def test_accepted_same_url_changed_time_creates_fresh_pending(self):
        store_review_candidates(
            self.conn, [_candidate(source_url=self.SAME_URL, start_time="10:00", end_time="11:00")],
            discovered_at=DISCOVERED_AT,
        )
        self.conn.execute("UPDATE event_review_candidates SET status = 'accepted'")
        self.conn.commit()
        changed = _candidate(source_url=self.SAME_URL, start_time="17:00", end_time="18:00")
        result = store_review_candidates(self.conn, [changed], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(len(result["stored"]), 1)
        self.assertEqual(len(_pending_rows(self.conn)), 1)

    def test_resolved_same_url_changed_location_creates_fresh_pending(self):
        store_review_candidates(
            self.conn, [_candidate(source_url=self.SAME_URL, location="Tempe")], discovered_at=DISCOVERED_AT,
        )
        self.conn.execute("UPDATE event_review_candidates SET status = 'dismissed'")
        self.conn.commit()
        changed = _candidate(source_url=self.SAME_URL, location="Downtown Phoenix")
        result = store_review_candidates(self.conn, [changed], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(len(result["stored"]), 1)
        self.assertEqual(len(_pending_rows(self.conn)), 1)

    def test_repeated_discovery_of_new_snapshot_does_not_duplicate(self):
        store_review_candidates(
            self.conn, [_candidate(source_url=self.SAME_URL, start_date="2026-09-20")], discovered_at=DISCOVERED_AT,
        )
        self.conn.execute("UPDATE event_review_candidates SET status = 'dismissed'")
        self.conn.commit()
        changed = _candidate(source_url=self.SAME_URL, start_date="2026-10-15")
        store_review_candidates(self.conn, [changed], discovered_at="2026-09-06T00:00:00+00:00")
        # discover the SAME new snapshot again - must not create a third row
        result = store_review_candidates(self.conn, [changed], discovered_at="2026-09-07T00:00:00+00:00")
        self.assertEqual(result["stored"], [])
        self.assertEqual(result["existing"][0]["status"], "pending")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_review_candidates").fetchone()[0], 2)

    def test_events_table_untouched_throughout(self):
        store_review_candidates(
            self.conn, [_candidate(source_url=self.SAME_URL, start_date="2026-09-20")], discovered_at=DISCOVERED_AT,
        )
        self.conn.execute("UPDATE event_review_candidates SET status = 'dismissed'")
        self.conn.commit()
        changed = _candidate(source_url=self.SAME_URL, start_date="2026-10-15")
        store_review_candidates(self.conn, [changed], discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


class NotEnteringEventsTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_review_candidate_never_enters_events_table(self):
        store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_missing_name_is_invalid(self):
        result = store_review_candidates(self.conn, [_candidate(name=None)], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["invalid_skipped"]), 1)
        self.assertEqual(_pending_rows(self.conn), [])

    def test_missing_source_url_is_invalid(self):
        result = store_review_candidates(self.conn, [_candidate(source_url=None)], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["invalid_skipped"]), 1)

    def test_dated_candidate_with_unparseable_time_and_no_all_day_is_invalid(self):
        candidate = _candidate(start_time=None, end_time=None, timezone=None)
        result = store_review_candidates(self.conn, [candidate], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["invalid_skipped"]), 1)
        self.assertEqual(_pending_rows(self.conn), [])

    def test_dateless_candidate_with_no_time_is_still_valid(self):
        # no start_date at all -> can't become a false all-day calendar
        # entry, so it's fine to queue for review as genuinely incomplete.
        candidate = _candidate(start_date=None, end_date=None, start_time=None, end_time=None, timezone=None)
        result = store_review_candidates(self.conn, [candidate], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["stored"]), 1)

    def test_explicit_all_day_candidate_is_valid(self):
        candidate = _candidate(start_time=None, end_time=None, timezone=None, all_day=True)
        result = store_review_candidates(self.conn, [candidate], discovered_at=DISCOVERED_AT)
        self.assertEqual(len(result["stored"]), 1)

    def test_validate_review_candidate_pure_function(self):
        self.assertEqual(validate_review_candidate(_candidate()), (True, None))
        is_valid, reason = validate_review_candidate(_candidate(name=""))
        self.assertFalse(is_valid)
        self.assertIn("name", reason)


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_dry_run_writes_nothing(self):
        result = store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT, dry_run=True)
        self.assertEqual(len(result["stored"]), 1)
        self.assertIsNone(result["stored"][0]["review_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_review_candidates").fetchone()[0], 0)

    def test_dry_run_still_reports_existing_against_real_db(self):
        store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT)
        result = store_review_candidates(self.conn, [_candidate()], discovered_at=DISCOVERED_AT, dry_run=True)
        self.assertEqual(result["stored"], [])
        self.assertEqual(len(result["existing"]), 1)


if __name__ == "__main__":
    unittest.main()
