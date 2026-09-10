"""Tests for az_tech_council_discovery_runner.run_az_tech_council_discovery().
Uses in-memory SQLite and an injected fake collect_func - no network calls,
never touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_az_tech_council_discovery_runner -v
"""
import sqlite3
import unittest
from pathlib import Path

from az_tech_council_discovery_runner import run_az_tech_council_discovery
from event_review_resolver import accept_review_candidate

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
DISCOVERED_AT = "2026-09-09T00:00:00+00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _raw(**overrides):
    base = dict(
        name="September Tech Mixer: Networking with local software engineers",
        description="An evening of networking for the Arizona tech community.",
        date_text="September 23, 2026", time_text="5:30pm - 7:30pm",
        location_text="Southwest Mission Acceleration Center, Phoenix",
        source_url="https://www.aztechcouncil.org/event/tech-mixer",
        registration_url=None, organizer=None, categories=["community-tech-events"],
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

    def test_include_is_inserted_as_new_never_interested(self):
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["included"], 1)
        self.assertEqual(result["summary"]["inserted"], 1)
        row = self.conn.execute("SELECT * FROM events").fetchone()
        self.assertEqual(row["source"], "Arizona Technology Council")
        self.assertEqual(row["event_type"], "professional")
        self.assertEqual(row["status"], "new")

    def test_no_user_event_status_row_created(self):
        run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_event_status").fetchone()[0], 0)

    def test_no_users_table_touched(self):
        run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_review_not_inserted_into_events_but_stored_in_review_queue(self):
        # No strong career/technical keyword and no supporting category ->
        # falls through to the conservative "review" default.
        raw = _raw(name="AZTC Annual Golf Tournament Fundraiser", description="Join us for a day on the links.", categories=[])
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["review"], 1)
        self.assertEqual(result["summary"]["review_stored"], 1)
        self.assertEqual(result["summary"]["inserted"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        review_row = self.conn.execute("SELECT * FROM event_review_candidates").fetchone()
        self.assertEqual(review_row["name"], "AZTC Annual Golf Tournament Fundraiser")
        self.assertEqual(review_row["source"], "Arizona Technology Council")
        self.assertEqual(review_row["event_type"], "professional")
        self.assertEqual(review_row["status"], "pending")

    def test_repeated_discovery_does_not_duplicate_pending_review(self):
        raw = _raw(name="AZTC Annual Golf Tournament Fundraiser", description="Join us for a day on the links.", categories=[])
        run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at=DISCOVERED_AT)
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at="2026-09-10T00:00:00+00:00")
        self.assertEqual(result["summary"]["review_stored"], 0)
        self.assertEqual(result["summary"]["review_existing"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_review_candidates").fetchone()[0], 1)

    def test_exclude_not_inserted(self):
        raw = _raw(name="AZTC Member Yoga & Wellness Hour", description="A relaxing yoga session.", categories=[])
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["excluded"], 1)
        self.assertEqual(result["summary"]["inserted"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_duplicate_discovery_not_inserted_twice(self):
        run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT)
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at="2026-09-10T00:00:00+00:00")
        self.assertEqual(result["summary"]["inserted"], 0)
        self.assertEqual(result["summary"]["duplicates_skipped"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_cross_source_duplicate_by_name_date_location_detected(self):
        # Same real-world event already discovered via a different source
        # (e.g. ASU Events) must not be inserted a second time under this
        # source's name - event_dedup.match_reason never checks `source`.
        self.conn.execute(
            "INSERT INTO events (name, source, source_url, start_date, start_time, location, "
            "status, event_state, first_seen) VALUES "
            "('September Tech Mixer: Networking with local software engineers', 'ASU Events', "
            "'https://asuevents.asu.edu/event/tech-mixer', '2026-09-23', '17:30', "
            "'Southwest Mission Acceleration Center, Phoenix', 'new', 'active', datetime('now'))"
        )
        self.conn.commit()
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([_raw()]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["inserted"], 0)
        self.assertEqual(result["summary"]["duplicates_skipped"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_invalid_candidate_not_inserted(self):
        raw = _raw(time_text=None)  # no time, not confirmed all-day -> invalid
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["invalid_skipped"], 1)
        self.assertEqual(result["summary"]["inserted"], 0)

    def test_malformed_candidate_isolated_from_others(self):
        class _BadRaw(dict):
            def get(self, key, default=None):
                if key == "location_text":
                    raise RuntimeError("boom")
                return super().get(key, default)

        raws = [
            _BadRaw(_raw(source_url="https://www.aztechcouncil.org/event/bad")),
            _raw(source_url="https://www.aztechcouncil.org/event/good"),
        ]
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect(raws), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["inserted"], 1)
        self.assertEqual(result["summary"]["runner_errors"], 1)

    def test_empty_successful_source_result(self):
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([], pages_fetched=2), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"], {
            "pages_fetched": 2, "candidates_found": 0, "included": 0, "review": 0, "excluded": 0,
            "inserted": 0, "duplicates_skipped": 0, "review_stored": 0, "review_existing": 0,
            "invalid_skipped": 0, "runner_errors": 0,
        })
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_source_request_failure_yields_empty_result_not_a_crash(self):
        # Mirrors what az_tech_council_collector.collect_listing itself
        # returns when every fetch fails: zero candidates, no exception -
        # never a corrupted or partially-written run.
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([], pages_fetched=0), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["candidates_found"], 0)
        self.assertEqual(result["summary"]["runner_errors"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_pre_existing_events_untouched_by_a_failed_run(self):
        self.conn.execute(
            "INSERT INTO events (name, source, source_url, start_date, start_time, "
            "status, event_state, first_seen) VALUES "
            "('Pre-existing Event', 'Arizona Technology Council', 'https://www.aztechcouncil.org/event/pre-existing', "
            "'2026-08-01', '10:00', 'new', 'active', datetime('now'))"
        )
        self.conn.commit()
        run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([], pages_fetched=0), discovered_at=DISCOVERED_AT)
        row = self.conn.execute("SELECT * FROM events WHERE name = 'Pre-existing Event'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["event_state"], "active")

    def test_summary_counts_correct(self):
        raws = [
            _raw(name="Software Developer Networking Night", source_url="https://www.aztechcouncil.org/event/a"),
            _raw(name="AZTC Member Yoga Hour", description="A relaxing session.", categories=[], source_url="https://www.aztechcouncil.org/event/b"),
            _raw(name="AZTC Annual Golf Tournament Fundraiser", description="Join us for a day on the links.", categories=[], source_url="https://www.aztechcouncil.org/event/c"),
        ]
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect(raws, pages_fetched=2), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"], {
            "pages_fetched": 2, "candidates_found": 3, "included": 1, "review": 1, "excluded": 1,
            "inserted": 1, "duplicates_skipped": 0, "review_stored": 1, "review_existing": 0,
            "invalid_skipped": 0, "runner_errors": 0,
        })


class EventTypeEndToEndTests(unittest.TestCase):
    """V0.12A audit: event_type='professional' must survive every real
    step of the pipeline for a genuine AZ Tech Council candidate -
    normalize (inside the runner) -> review candidate storage -> review
    candidate load -> accept -> final events row. Exercises the actual
    production functions at every stage, not a synthetic fixture row."""

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_professional_event_type_survives_review_storage_and_accept(self):
        raw = _raw(
            name="AZTC Annual Golf Tournament Fundraiser",
            description="Join us for a day on the links.", categories=[],
        )
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect([raw]), discovered_at=DISCOVERED_AT)
        self.assertEqual(result["summary"]["review_stored"], 1)

        review_row = self.conn.execute("SELECT * FROM event_review_candidates").fetchone()
        self.assertEqual(review_row["event_type"], "professional")  # storage stage

        outcome = accept_review_candidate(self.conn, review_row["id"], resolved_at="2026-09-10T00:00:00+00:00")
        self.assertIsNotNone(outcome["event_id"])

        event_row = self.conn.execute("SELECT * FROM events WHERE id = ?", (outcome["event_id"],)).fetchone()
        self.assertEqual(event_row["event_type"], "professional")  # final events row
        self.assertEqual(event_row["source"], "Arizona Technology Council")
        self.assertEqual(event_row["status"], "new")  # never auto-Interested


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_dry_run_zero_db_writes(self):
        raws = [
            _raw(),
            _raw(name="AZTC Annual Golf Tournament Fundraiser", description="Join us for a day on the links.", categories=[], source_url="https://www.aztechcouncil.org/event/b"),
        ]
        result = run_az_tech_council_discovery(self.conn, collect_func=_fake_collect(raws), discovered_at=DISCOVERED_AT, dry_run=True)
        self.assertEqual(result["summary"]["included"], 1)
        self.assertEqual(result["summary"]["review_stored"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM event_review_candidates").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_event_status").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
