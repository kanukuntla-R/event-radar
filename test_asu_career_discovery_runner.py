"""Tests for asu_career_discovery_runner.run_asu_career_discovery(). Uses
in-memory SQLite and an injected fake collect_func - no network calls, never
touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_asu_career_discovery_runner -v
"""
import sqlite3
import unittest
from pathlib import Path

from asu_career_discovery_runner import run_asu_career_discovery

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
DISCOVERED_AT = "2026-09-05T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _raw(**overrides):
    base = dict(
        name="FSE Workshop: Creating Your Technical Resume",
        date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST",
        location_text="Virtual", source_url="https://career.eoss.asu.edu/events/foo/",
    )
    base.update(overrides)
    return base


def _fake_collect(candidates, pages_fetched=1):
    def collect_func(**kwargs):
        return {"candidates": candidates, "pages_fetched": pages_fetched}
    return collect_func


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_collector_to_normalization_to_storage(self):
        result = run_asu_career_discovery(
            self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT
        )
        self.assertEqual(result["summary"]["inserted"], 1)
        row = self.conn.execute("SELECT * FROM events").fetchone()
        self.assertEqual(row["name"], "FSE Workshop: Creating Your Technical Resume")
        self.assertEqual(row["event_subtype"], "workshop")
        self.assertEqual(row["location_area"], "online")
        self.assertEqual(row["status"], "new")

    def test_summary_counts_correct(self):
        raws = [
            _raw(name="New Event", source_url="https://career.eoss.asu.edu/events/a/"),
            _raw(name="Missing Date", date_text=None, source_url="https://career.eoss.asu.edu/events/b/"),
        ]
        result = run_asu_career_discovery(self.conn, collect_func=_fake_collect(raws, pages_fetched=2), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"], {
            "pages_fetched": 2, "candidates_found": 2, "inserted": 1,
            "duplicates_skipped": 0, "invalid_skipped": 1, "runner_errors": 0,
        })

    def test_duplicate_reporting(self):
        run_asu_career_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT)
        result = run_asu_career_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["summary"]["inserted"], 0)
        self.assertEqual(result["summary"]["duplicates_skipped"], 1)
        duplicate_events = [e for e in result["events"] if e["result"] == "duplicate"]
        self.assertEqual(len(duplicate_events), 1)
        self.assertIsNotNone(duplicate_events[0]["existing_event_id"])

    def test_bad_candidate_isolated_from_others(self):
        class _BadRaw(dict):
            # raises mid-normalization but leaves "name" lookups (used in
            # both the try body and the except handler) working normally.
            def get(self, key, default=None):
                if key == "location_text":
                    raise RuntimeError("boom")
                return super().get(key, default)

        raws = [_BadRaw(_raw(source_url="https://career.eoss.asu.edu/events/bad/")), _raw(source_url="https://career.eoss.asu.edu/events/good/")]
        result = run_asu_career_discovery(self.conn, collect_func=_fake_collect(raws), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["inserted"], 1)
        self.assertEqual(result["summary"]["runner_errors"], 1)


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_dry_run_zero_db_writes(self):
        result = run_asu_career_discovery(
            self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT, dry_run=True
        )
        self.assertEqual(result["summary"]["inserted"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_dry_run_still_performs_discovery(self):
        result = run_asu_career_discovery(
            self.conn, collect_func=_fake_collect([_raw()], pages_fetched=3), discovered_at=DISCOVERED_AT, dry_run=True
        )
        self.assertEqual(result["summary"]["pages_fetched"], 3)
        self.assertEqual(result["summary"]["candidates_found"], 1)


if __name__ == "__main__":
    unittest.main()
