"""Tests for hackathon_source_recovery_runner.run_source_recovery(). Uses
in-memory SQLite and injected fake discover functions - no network calls,
never touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_hackathon_source_recovery_runner -v
"""
import sqlite3
import unittest
from pathlib import Path

from hackathon_source_recovery_runner import run_source_recovery

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_event(conn, **overrides):
    fields = dict(
        name="Test Hack", event_type="hackathon", event_state="active",
        source_url="https://old-site.example.com", organizer="Major League Hacking",
        consecutive_failures=3,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


def _event(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def _pending_candidates(conn, event_id):
    return conn.execute(
        "SELECT * FROM source_recovery_candidates WHERE event_id = ? AND status = 'pending'",
        (event_id,),
    ).fetchall()


def _entry_for(result, event_id):
    return next(e for e in result["events"] if e["event_id"] == event_id)


class _FakeDiscover:
    def __init__(self, hits=None, by_name=None, raise_for_name=None):
        self.hits = hits if hits is not None else []
        self.by_name = by_name or {}
        self.raise_for_name = raise_for_name
        self.calls = []

    def __call__(self, event, now=None):
        self.calls.append(event["name"])
        if self.raise_for_name and event["name"] == self.raise_for_name:
            raise RuntimeError(f"simulated discovery bug for {event['name']}")
        return self.by_name.get(event["name"], self.hits)


MLH_HIT = {"candidate_url": "https://mlh.io/events/test", "title": "Test Hack - MLH", "evidence": "listed on MLH"}


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_below_threshold_not_eligible(self):
        _insert_event(self.conn, consecutive_failures=2)
        discover = _FakeDiscover()
        result = run_source_recovery(self.conn, discover_func=discover)
        self.assertEqual(result["summary"]["eligible"], 0)
        self.assertEqual(discover.calls, [])

    def test_at_or_above_threshold_is_eligible(self):
        event_id = _insert_event(self.conn, consecutive_failures=3)
        discover = _FakeDiscover()
        result = run_source_recovery(self.conn, discover_func=discover)
        self.assertEqual(result["summary"]["eligible"], 1)
        self.assertEqual(discover.calls, ["Test Hack"])
        self.assertEqual(_entry_for(result, event_id)["result"], "processed")

    def test_inactive_event_skipped(self):
        _insert_event(self.conn, event_state="inactive", consecutive_failures=5)
        discover = _FakeDiscover()
        result = run_source_recovery(self.conn, discover_func=discover)
        self.assertEqual(result["summary"]["eligible"], 0)
        self.assertEqual(discover.calls, [])

    def test_non_hackathon_event_type_skipped(self):
        _insert_event(self.conn, event_type="conference", consecutive_failures=5)
        discover = _FakeDiscover()
        result = run_source_recovery(self.conn, discover_func=discover)
        self.assertEqual(result["summary"]["eligible"], 0)


class DiscoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_candidates_stored_for_eligible_event(self):
        event_id = _insert_event(self.conn)
        discover = _FakeDiscover(hits=[MLH_HIT])
        result = run_source_recovery(self.conn, discover_func=discover)
        entry = _entry_for(result, event_id)
        self.assertEqual(entry["candidates_found"], 1)
        self.assertEqual(entry["candidates_inserted"], 1)
        rows = _pending_candidates(self.conn, event_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_type"], "mlh")

    def test_no_hits_stores_nothing(self):
        event_id = _insert_event(self.conn)
        discover = _FakeDiscover(hits=[])
        result = run_source_recovery(self.conn, discover_func=discover)
        entry = _entry_for(result, event_id)
        self.assertEqual(entry["candidates_found"], 0)
        self.assertEqual(_pending_candidates(self.conn, event_id), [])

    def test_source_url_never_changes_during_discovery(self):
        event_id = _insert_event(self.conn, source_url="https://old-site.example.com")
        discover = _FakeDiscover(hits=[MLH_HIT])
        run_source_recovery(self.conn, discover_func=discover)
        self.assertEqual(_event(self.conn, event_id)["source_url"], "https://old-site.example.com")

    def test_duplicate_candidates_not_duplicated_across_runs(self):
        event_id = _insert_event(self.conn)
        discover = _FakeDiscover(hits=[MLH_HIT])
        run_source_recovery(self.conn, discover_func=discover)
        second = run_source_recovery(self.conn, discover_func=discover)
        entry = _entry_for(second, event_id)
        self.assertEqual(entry["candidates_inserted"], 0)
        self.assertEqual(entry["duplicates_skipped"], 1)
        self.assertEqual(len(_pending_candidates(self.conn, event_id)), 1)

    def test_one_event_exception_does_not_stop_others(self):
        broken_id = _insert_event(self.conn, name="Broken Hack")
        healthy_id = _insert_event(self.conn, name="Healthy Hack")
        discover = _FakeDiscover(by_name={"Healthy Hack": [MLH_HIT]}, raise_for_name="Broken Hack")
        result = run_source_recovery(self.conn, discover_func=discover)
        self.assertEqual(_entry_for(result, broken_id)["result"], "runner_error")
        self.assertEqual(_entry_for(result, healthy_id)["result"], "processed")
        self.assertEqual(len(discover.calls), 2)
        self.assertEqual(result["summary"]["runner_errors"], 1)


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_dry_run_never_calls_discover(self):
        _insert_event(self.conn)
        discover = _FakeDiscover(hits=[MLH_HIT])
        run_source_recovery(self.conn, discover_func=discover, dry_run=True)
        self.assertEqual(discover.calls, [])

    def test_dry_run_makes_zero_writes(self):
        event_id = _insert_event(self.conn)
        discover = _FakeDiscover(hits=[MLH_HIT])
        result = run_source_recovery(self.conn, discover_func=discover, dry_run=True)
        self.assertEqual(_entry_for(result, event_id)["result"], "eligible")
        self.assertEqual(_pending_candidates(self.conn, event_id), [])
        self.assertEqual(_event(self.conn, event_id)["source_url"], "https://old-site.example.com")


if __name__ == "__main__":
    unittest.main()
