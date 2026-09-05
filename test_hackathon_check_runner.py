"""Tests for hackathon_check_runner.run_due_hackathon_checks(). Uses
in-memory SQLite and injected fake collector functions - no network calls,
never touches data/events.db. Stdlib unittest only.

Run with: python -m unittest test_hackathon_check_runner -v
"""
import sqlite3
import unittest
from datetime import date
from pathlib import Path

from hackathon_check_runner import run_due_hackathon_checks

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
TODAY = date(2026, 9, 5)
NOW_ISO = "2026-09-05T00:00:00+00:00"

EMPTY_DETECTED = {
    "start_date": None, "end_date": None, "date_status": None,
    "registration_status": None, "registration_url": None,
    "application_deadline": None,
}


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _insert_event(conn, **overrides):
    fields = dict(
        name="Test Hack", event_type="hackathon", event_state="active",
        date_status="confirmed", start_date="2026-10-03", end_date="2026-10-04",
        registration_status="open", registration_url=None, application_deadline=None,
        source_url="https://example.com/hack", last_checked=None,
        status="new", consecutive_failures=0,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


def _event(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def _pending_proposals(conn, event_id):
    return conn.execute(
        "SELECT * FROM event_change_proposals WHERE event_id = ? AND status = 'pending'",
        (event_id,),
    ).fetchall()


def _entry_for(result, event_id):
    return next(e for e in result["events"] if e["event_id"] == event_id)


def _collector_result(source_result="ok", detected=None, checked_at=NOW_ISO, raw_evidence=None):
    d = dict(EMPTY_DETECTED)
    if detected:
        d.update(detected)
    return {
        "source_result": source_result,
        "requested_url": "https://example.com/hack",
        "final_url": "https://example.com/hack",
        "http_status": 200 if source_result in ("ok", "redirected") else None,
        "checked_at": checked_at,
        "page_title": "Example",
        "raw_evidence": raw_evidence or {},
        "detected": d,
        "notes": [],
    }


class _CountingCollector:
    """Fake collector: returns a canned result (by event name, or one
    default), records every call, and can raise for a chosen event name."""

    def __init__(self, default_result=None, by_name=None, raise_for_name=None):
        self.default_result = default_result or _collector_result()
        self.by_name = by_name or {}
        self.raise_for_name = raise_for_name
        self.calls = []

    def __call__(self, event, now=None):
        self.calls.append((event["name"], now))
        if self.raise_for_name and event["name"] == self.raise_for_name:
            raise RuntimeError(f"simulated collector bug for {event['name']}")
        return self.by_name.get(event["name"], self.default_result)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_processes_only_event_type_hackathon(self):
        hack_id = _insert_event(self.conn, name="A Hack", last_checked=None)
        _insert_event(self.conn, name="A Conference", event_type="conference", last_checked=None)
        collector = _CountingCollector()
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(result["summary"]["total_active_hackathons"], 1)
        self.assertEqual([e["event_id"] for e in result["events"]], [hack_id])

    def test_skips_inactive_hackathons(self):
        _insert_event(self.conn, name="Dead Hack", event_state="inactive", last_checked=None)
        collector = _CountingCollector()
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(result["summary"]["total_active_hackathons"], 0)
        self.assertEqual(result["events"], [])
        self.assertEqual(collector.calls, [])

    def test_empty_hackathon_database_works_cleanly(self):
        collector = _CountingCollector()
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(result["events"], [])
        self.assertEqual(
            result["summary"],
            {
                "total_active_hackathons": 0, "due": 0, "checked": 0, "skipped": 0,
                "source_success": 0, "source_failure": 0, "runner_errors": 0,
                "proposals_inserted": 0, "proposals_skipped_duplicate": 0,
            },
        )


class StrategyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_skips_events_when_strategy_says_not_due(self):
        event_id = _insert_event(
            self.conn, registration_status="open", last_checked="2026-09-04"  # 1 day ago, cadence 7
        )
        collector = _CountingCollector()
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        entry = _entry_for(result, event_id)
        self.assertEqual(entry["result"], "skipped")
        self.assertEqual(entry["strategy_reason"], "checked_recently")
        self.assertIsNotNone(entry["next_check"])

    def test_skipped_event_never_calls_collector(self):
        _insert_event(self.conn, registration_status="open", last_checked="2026-09-04")
        collector = _CountingCollector()
        run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(collector.calls, [])

    def test_due_event_calls_collector(self):
        event_id = _insert_event(self.conn, last_checked=None)
        collector = _CountingCollector()
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(len(collector.calls), 1)
        entry = _entry_for(result, event_id)
        self.assertEqual(entry["result"], "checked")
        self.assertTrue(entry["strategy_reason"].endswith("never_checked"))

    def test_today_and_now_are_passed_through(self):
        event_id = _insert_event(self.conn, last_checked=None)
        collector = _CountingCollector()
        run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(collector.calls, [("Test Hack", NOW_ISO)])
        self.assertEqual(_event(self.conn, event_id)["last_checked"], NOW_ISO)


class SourceHealthTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def _run_with_result(self, source_result):
        event_id = _insert_event(self.conn, last_checked=None)
        collector = _CountingCollector(default_result=_collector_result(source_result))
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        return event_id, result

    def test_ok_result_records_health(self):
        event_id, result = self._run_with_result("ok")
        row = _event(self.conn, event_id)
        self.assertEqual(row["last_check_status"], "ok")
        self.assertIsNotNone(row["last_checked"])
        self.assertIsNotNone(row["last_successful_check"])
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(result["summary"]["source_success"], 1)

    def test_redirected_counts_as_success(self):
        event_id, result = self._run_with_result("redirected")
        self.assertEqual(_event(self.conn, event_id)["last_check_status"], "redirected")
        self.assertEqual(result["summary"]["source_success"], 1)
        self.assertEqual(result["summary"]["source_failure"], 0)

    def test_unavailable_counts_as_source_failure(self):
        event_id, result = self._run_with_result("unavailable")
        row = _event(self.conn, event_id)
        self.assertEqual(row["last_check_status"], "unavailable")
        self.assertEqual(row["consecutive_failures"], 1)
        self.assertEqual(result["summary"]["source_failure"], 1)

    def test_not_found_counts_as_source_failure(self):
        event_id, result = self._run_with_result("not_found")
        self.assertEqual(_event(self.conn, event_id)["consecutive_failures"], 1)
        self.assertEqual(result["summary"]["source_failure"], 1)

    def test_parse_failed_counts_as_source_failure(self):
        event_id, result = self._run_with_result("parse_failed")
        self.assertEqual(_event(self.conn, event_id)["consecutive_failures"], 1)
        self.assertEqual(result["summary"]["source_failure"], 1)

    def test_source_failure_does_not_stop_later_events(self):
        failing_id = _insert_event(self.conn, name="Flaky Hack", last_checked=None)
        healthy_id = _insert_event(self.conn, name="Healthy Hack", last_checked=None)
        collector = _CountingCollector(by_name={
            "Flaky Hack": _collector_result("unavailable"),
            "Healthy Hack": _collector_result("ok"),
        })
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(_entry_for(result, failing_id)["result"], "checked")
        self.assertEqual(_entry_for(result, healthy_id)["result"], "checked")
        self.assertEqual(_event(self.conn, healthy_id)["last_check_status"], "ok")
        self.assertEqual(len(collector.calls), 2)


class ProposalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_detected_changes_create_pending_proposals(self):
        event_id = _insert_event(self.conn, registration_status="unknown", last_checked=None)
        collector = _CountingCollector(default_result=_collector_result(
            "ok", detected={"registration_status": "open"}, raw_evidence={"registration_phrase": "apply now"}
        ))
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        entry = _entry_for(result, event_id)
        self.assertEqual(entry["proposals_inserted"], 1)
        self.assertEqual(entry["proposals_skipped_duplicate"], 0)
        rows = _pending_proposals(self.conn, event_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["new_value"], "open")

    def test_no_detected_changes_create_no_proposals(self):
        event_id = _insert_event(self.conn, registration_status="open", last_checked=None)
        collector = _CountingCollector(default_result=_collector_result(
            "ok", detected={"registration_status": "open"}
        ))
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        entry = _entry_for(result, event_id)
        self.assertEqual(entry["proposals_inserted"], 0)
        self.assertEqual(_pending_proposals(self.conn, event_id), [])

    def test_duplicate_pending_proposals_are_not_duplicated(self):
        event_id = _insert_event(self.conn, registration_status="unknown", last_checked=None)
        collector = _CountingCollector(default_result=_collector_result(
            "ok", detected={"registration_status": "open"}, raw_evidence={"registration_phrase": "apply now"}
        ))
        first = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(_entry_for(first, event_id)["proposals_inserted"], 1)

        # 7 days later: confirmed+unknown-registration cadence is due again,
        # website state (per the fake collector) hasn't changed.
        second_today = date(2026, 9, 12)
        second = run_due_hackathon_checks(
            self.conn, today=second_today, now="2026-09-12T00:00:00+00:00", collector_func=collector
        )
        entry = _entry_for(second, event_id)
        self.assertEqual(entry["proposals_inserted"], 0)
        self.assertEqual(entry["proposals_skipped_duplicate"], 1)
        self.assertEqual(len(_pending_proposals(self.conn, event_id)), 1)

    def test_event_facts_remain_unchanged(self):
        event_id = _insert_event(self.conn, registration_status="unknown", last_checked=None)
        collector = _CountingCollector(default_result=_collector_result(
            "ok", detected={"registration_status": "open"}, raw_evidence={"registration_phrase": "apply now"}
        ))
        run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(_event(self.conn, event_id)["registration_status"], "unknown")

    def test_event_status_remains_unchanged(self):
        event_id = _insert_event(self.conn, status="watching", last_checked=None)
        collector = _CountingCollector(default_result=_collector_result("unavailable"))
        run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(_event(self.conn, event_id)["status"], "watching")

    def test_event_state_and_inactive_reason_remain_unchanged(self):
        event_id = _insert_event(self.conn, event_state="active", last_checked=None)
        collector = _CountingCollector(default_result=_collector_result("not_found"))
        run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        row = _event(self.conn, event_id)
        self.assertEqual(row["event_state"], "active")
        self.assertIsNone(row["inactive_reason"])


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_summary_counts_are_correct(self):
        skipped_id = _insert_event(
            self.conn, name="Skip Me", registration_status="open", last_checked="2026-09-04"
        )
        ok_id = _insert_event(self.conn, name="OK Hack", registration_status="unknown", last_checked=None)
        fail_id = _insert_event(self.conn, name="Fail Hack", last_checked=None)
        collector = _CountingCollector(by_name={
            "OK Hack": _collector_result("ok", detected={"registration_status": "open"},
                                          raw_evidence={"registration_phrase": "apply now"}),
            "Fail Hack": _collector_result("unavailable"),
        })
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)
        self.assertEqual(result["summary"], {
            "total_active_hackathons": 3,
            "due": 2,
            "checked": 2,
            "skipped": 1,
            "source_success": 1,
            "source_failure": 1,
            "runner_errors": 0,
            "proposals_inserted": 1,
            "proposals_skipped_duplicate": 0,
        })


class ExceptionIsolationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_unexpected_exception_does_not_abort_batch(self):
        broken_id = _insert_event(self.conn, name="Broken Hack", last_checked=None)
        healthy_id = _insert_event(self.conn, name="Healthy Hack", last_checked=None)
        collector = _CountingCollector(raise_for_name="Broken Hack")
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)

        self.assertEqual(_entry_for(result, healthy_id)["result"], "checked")
        self.assertEqual(len(collector.calls), 2)

    def test_runner_error_is_reported_with_no_fabricated_source_result(self):
        broken_id = _insert_event(self.conn, name="Broken Hack", last_checked=None)
        collector = _CountingCollector(raise_for_name="Broken Hack")
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)

        entry = _entry_for(result, broken_id)
        self.assertEqual(entry["result"], "runner_error")
        self.assertIsNone(entry["source_result"])
        self.assertIn("Broken Hack", entry["error"])
        self.assertEqual(result["summary"]["runner_errors"], 1)

    def test_runner_error_event_facts_and_health_untouched(self):
        broken_id = _insert_event(self.conn, name="Broken Hack", last_checked=None, registration_status="unknown")
        collector = _CountingCollector(raise_for_name="Broken Hack")
        run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector)

        row = _event(self.conn, broken_id)
        self.assertIsNone(row["last_checked"])
        self.assertIsNone(row["last_check_status"])
        self.assertEqual(row["registration_status"], "unknown")
        self.assertEqual(_pending_proposals(self.conn, broken_id), [])


class NoAutoInactivationTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_repeated_source_failures_never_auto_inactivate(self):
        event_id = _insert_event(self.conn, name="Flaky Hack", registration_status="unknown")
        collector = _CountingCollector(default_result=_collector_result("unavailable"))

        for day_offset, day in enumerate((5, 12, 19, 26)):
            today = date(2026, 9, day)
            run_due_hackathon_checks(self.conn, today=today, now=NOW_ISO, collector_func=collector)
            row = _event(self.conn, event_id)
            self.assertEqual(row["event_state"], "active")
            self.assertIsNone(row["inactive_reason"])

        self.assertGreaterEqual(_event(self.conn, event_id)["consecutive_failures"], 3)


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_dry_run_never_calls_collector(self):
        _insert_event(self.conn, last_checked=None)
        collector = _CountingCollector()
        run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector, dry_run=True)
        self.assertEqual(collector.calls, [])

    def test_dry_run_reports_would_check_and_skipped(self):
        due_id = _insert_event(self.conn, name="Due Hack", last_checked=None)
        skipped_id = _insert_event(
            self.conn, name="Skip Hack", registration_status="open", last_checked="2026-09-04"
        )
        collector = _CountingCollector()
        result = run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector, dry_run=True)
        self.assertEqual(_entry_for(result, due_id)["result"], "would_check")
        self.assertEqual(_entry_for(result, skipped_id)["result"], "skipped")
        self.assertEqual(result["summary"]["would_check"], 1)
        self.assertEqual(result["summary"]["skipped"], 1)

    def test_dry_run_makes_zero_writes(self):
        event_id = _insert_event(self.conn, last_checked=None, registration_status="unknown")
        collector = _CountingCollector(default_result=_collector_result(
            "ok", detected={"registration_status": "open"}
        ))
        run_due_hackathon_checks(self.conn, today=TODAY, now=NOW_ISO, collector_func=collector, dry_run=True)
        row = _event(self.conn, event_id)
        self.assertIsNone(row["last_checked"])
        self.assertIsNone(row["last_check_status"])
        self.assertEqual(row["registration_status"], "unknown")
        self.assertEqual(_pending_proposals(self.conn, event_id), [])


if __name__ == "__main__":
    unittest.main()
