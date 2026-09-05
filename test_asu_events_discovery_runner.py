"""Tests for asu_events_discovery_runner.run_asu_events_discovery(). Uses
in-memory SQLite and an injected fake collect_func - no network calls, never
touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_asu_events_discovery_runner -v
"""
import sqlite3
import unittest
from pathlib import Path

from asu_events_discovery_runner import run_asu_events_discovery

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
DISCOVERED_AT = "2026-09-05T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _raw(**overrides):
    base = dict(
        name="Fall 2026 Career & Internship Fair",
        date_text="Tue, Sep 8, 2026", time_text="10:00 am &ndash; 2:00 pm (MST)",
        location_text="Tempe campus", source_url="https://asuevents.asu.edu/event/career-fair",
        categories=["Professional and career development"],
    )
    base.update(overrides)
    return base


def _fake_collect(candidates, pages_fetched=1):
    def collect_func(**kwargs):
        return {"candidates": candidates, "pages_fetched": pages_fetched}
    return collect_func


def _entries(result, kind):
    return [e for e in result["events"] if e["result"] == kind]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_include_is_inserted(self):
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["included"], 1)
        self.assertEqual(result["summary"]["inserted"], 1)
        row = self.conn.execute("SELECT * FROM events").fetchone()
        self.assertEqual(row["source"], "ASU Events")
        self.assertEqual(row["event_type"], "asu")
        self.assertEqual(row["status"], "new")

    def test_review_not_inserted(self):
        raw = _raw(name="Maosho", categories=["Academic events"])
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["review"], 1)
        self.assertEqual(result["summary"]["inserted"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(_entries(result, "review")[0]["name"], "Maosho")

    def test_exclude_not_inserted(self):
        raw = _raw(name="Sun Devil Soccer vs. North Carolina", categories=["Sports"])
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["excluded"], 1)
        self.assertEqual(result["summary"]["inserted"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_duplicate_not_inserted(self):
        run_asu_events_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT)
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at="2026-09-06T00:00:00+00:00")
        self.assertEqual(result["summary"]["inserted"], 0)
        self.assertEqual(result["summary"]["duplicates_skipped"], 1)

    def test_invalid_not_inserted(self):
        raw = _raw(time_text="Times vary")  # unparseable, not all-day -> invalid
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["invalid_skipped"], 1)
        self.assertEqual(result["summary"]["inserted"], 0)

    def test_summary_counts_correct(self):
        raws = [
            _raw(name="Career Fair A", source_url="https://asuevents.asu.edu/event/a"),
            _raw(name="Sun Devil Soccer", categories=["Sports"], source_url="https://asuevents.asu.edu/event/b"),
            _raw(name="Maosho", categories=["Academic events"], source_url="https://asuevents.asu.edu/event/c"),
        ]
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect(raws, pages_fetched=2), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"], {
            "pages_fetched": 2, "candidates_found": 3, "included": 1, "review": 1, "excluded": 1,
            "inserted": 1, "duplicates_skipped": 0, "invalid_skipped": 0, "runner_errors": 0,
        })

    def test_bad_candidate_isolated_from_others(self):
        class _BadRaw(dict):
            def get(self, key, default=None):
                if key == "location_text":
                    raise RuntimeError("boom")
                return super().get(key, default)

        raws = [
            _BadRaw(_raw(source_url="https://asuevents.asu.edu/event/bad")),
            _raw(source_url="https://asuevents.asu.edu/event/good"),
        ]
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect(raws), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["inserted"], 1)
        self.assertEqual(result["summary"]["runner_errors"], 1)


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_dry_run_zero_db_writes(self):
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT, dry_run=True)
        self.assertEqual(result["summary"]["included"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_dry_run_still_performs_discovery_and_relevance(self):
        raws = [_raw(), _raw(name="Sun Devil Soccer", categories=["Sports"], source_url="https://asuevents.asu.edu/event/b")]
        result = run_asu_events_discovery(self.conn, collect_func=_fake_collect(raws, pages_fetched=3), discovered_at=DISCOVERED_AT, dry_run=True)
        self.assertEqual(result["summary"]["pages_fetched"], 3)
        self.assertEqual(result["summary"]["included"], 1)
        self.assertEqual(result["summary"]["excluded"], 1)


if __name__ == "__main__":
    unittest.main()
