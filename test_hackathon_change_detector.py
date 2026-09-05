"""Tests for hackathon_change_detector.detect_changes(). Pure logic, no
SQLite, no HTTP. Stdlib unittest only.

Run with: python -m unittest test_hackathon_change_detector -v
"""
import copy
import unittest

from hackathon_change_detector import detect_changes


def _event(**overrides):
    base = dict(
        name="Test Hack",
        date_status="tba",
        start_date=None,
        end_date=None,
        registration_status="unknown",
        registration_url=None,
        application_deadline=None,
    )
    base.update(overrides)
    return base


def _collector_result(source_result="ok", detected=None, raw_evidence=None):
    base_detected = {
        "start_date": None, "end_date": None, "date_status": None,
        "registration_status": None, "registration_url": None,
        "application_deadline": None,
    }
    if detected:
        base_detected.update(detected)
    return {
        "source_result": source_result,
        "requested_url": "https://example.com/hack",
        "final_url": "https://example.com/hack",
        "http_status": 200,
        "checked_at": "2026-09-04T00:00:00+00:00",
        "page_title": "Example Hack",
        "raw_evidence": raw_evidence or {},
        "detected": base_detected,
        "notes": [],
    }


def _change_for(result, field):
    return next((c for c in result["changes"] if c["field"] == field), None)


class NoChangeTests(unittest.TestCase):
    def test_no_changes_when_values_match(self):
        event = _event(registration_status="open")
        result = _collector_result(detected={"registration_status": "open"})
        outcome = detect_changes(event, result)
        self.assertFalse(outcome["has_changes"])
        self.assertEqual(outcome["changes"], [])
        self.assertIn("registration_status", outcome["unchanged_fields"])

    def test_collector_none_does_not_erase_existing_dates(self):
        event = _event(
            date_status="confirmed", start_date="2026-10-03", end_date="2026-10-04"
        )
        result = _collector_result()  # detected all None
        outcome = detect_changes(event, result)
        self.assertFalse(outcome["has_changes"])
        self.assertEqual(outcome["changes"], [])
        for field in ("start_date", "end_date", "date_status"):
            self.assertIn(field, outcome["ignored_fields"])


class ProposedChangeTests(unittest.TestCase):
    def test_registration_status_unknown_to_open(self):
        event = _event(registration_status="unknown")
        result = _collector_result(
            detected={"registration_status": "open"},
            raw_evidence={"registration_phrase": "apply today"},
        )
        outcome = detect_changes(event, result)
        self.assertTrue(outcome["has_changes"])
        change = _change_for(outcome, "registration_status")
        self.assertEqual(change, {
            "field": "registration_status", "old_value": "unknown",
            "new_value": "open", "evidence": "apply today",
        })

    def test_registration_url_none_to_detected(self):
        event = _event(registration_url=None)
        result = _collector_result(
            detected={"registration_url": "https://example.com/apply"},
            raw_evidence={"registration_link_text": "apply now"},
        )
        outcome = detect_changes(event, result)
        change = _change_for(outcome, "registration_url")
        self.assertEqual(change["old_value"], None)
        self.assertEqual(change["new_value"], "https://example.com/apply")
        self.assertEqual(change["evidence"], "apply now")

    def test_tba_event_gets_confirmed_dates(self):
        event = _event(date_status="tba", start_date=None, end_date=None)
        result = _collector_result(
            detected={
                "start_date": "2026-10-03", "end_date": "2026-10-04",
                "date_status": "confirmed",
            },
            raw_evidence={"date_match_text": "October 3-4, 2026"},
        )
        outcome = detect_changes(event, result)
        self.assertTrue(outcome["has_changes"])
        fields_changed = {c["field"] for c in outcome["changes"]}
        self.assertEqual(fields_changed, {"start_date", "end_date", "date_status"})
        for c in outcome["changes"]:
            self.assertEqual(c["evidence"], "October 3-4, 2026")

    def test_application_deadline_added(self):
        event = _event(application_deadline=None)
        result = _collector_result(
            detected={"application_deadline": "2026-09-30"},
            raw_evidence={"deadline_match_text": "Application deadline: Sept 30, 2026"},
        )
        outcome = detect_changes(event, result)
        change = _change_for(outcome, "application_deadline")
        self.assertEqual(change["new_value"], "2026-09-30")
        self.assertEqual(change["evidence"], "Application deadline: Sept 30, 2026")


class SourceFailureTests(unittest.TestCase):
    def _assert_no_fact_changes(self, source_result):
        event = _event(registration_status="unknown")
        # even if detected happens to carry values, a failed source must not propose changes
        result = _collector_result(
            source_result=source_result,
            detected={"registration_status": "open"},
            raw_evidence={"registration_phrase": "apply today"},
        )
        outcome = detect_changes(event, result)
        self.assertFalse(outcome["has_changes"])
        self.assertEqual(outcome["changes"], [])
        self.assertEqual(outcome["source_result"], source_result)
        self.assertEqual(set(outcome["ignored_fields"]), set(
            ("start_date", "end_date", "date_status", "registration_status",
             "registration_url", "application_deadline")
        ))

    def test_unavailable_source_no_fact_changes(self):
        self._assert_no_fact_changes("unavailable")

    def test_not_found_source_no_fact_changes(self):
        self._assert_no_fact_changes("not_found")

    def test_parse_failed_source_no_fact_changes(self):
        self._assert_no_fact_changes("parse_failed")

    def test_redirected_source_still_allows_comparison(self):
        event = _event(registration_status="unknown")
        result = _collector_result(
            source_result="redirected",
            detected={"registration_status": "open"},
            raw_evidence={"registration_phrase": "apply today"},
        )
        outcome = detect_changes(event, result)
        self.assertTrue(outcome["has_changes"])
        self.assertEqual(outcome["source_result"], "redirected")


class NormalizationTests(unittest.TestCase):
    def test_empty_detected_string_treated_as_missing(self):
        event = _event(registration_url=None)
        result = _collector_result(detected={"registration_url": ""})
        outcome = detect_changes(event, result)
        self.assertFalse(outcome["has_changes"])
        self.assertIn("registration_url", outcome["ignored_fields"])

    def test_empty_detected_string_does_not_erase_existing_value(self):
        event = _event(registration_url="https://example.com/old")
        result = _collector_result(detected={"registration_url": ""})
        outcome = detect_changes(event, result)
        self.assertFalse(outcome["has_changes"])
        self.assertIn("registration_url", outcome["ignored_fields"])

    def test_empty_string_on_event_side_is_not_a_meaningful_old_value(self):
        event = _event(registration_url="")
        result = _collector_result(
            detected={"registration_url": "https://example.com/apply"},
            raw_evidence={"registration_link_text": "apply"},
        )
        outcome = detect_changes(event, result)
        change = _change_for(outcome, "registration_url")
        self.assertEqual(change["old_value"], None)


class MutationSafetyTests(unittest.TestCase):
    def test_does_not_mutate_event_or_collector_result(self):
        event = _event(registration_status="unknown")
        result = _collector_result(
            detected={"registration_status": "open", "registration_url": "https://x/apply"},
            raw_evidence={"registration_phrase": "apply today", "registration_link_text": "apply"},
        )
        event_before = copy.deepcopy(event)
        result_before = copy.deepcopy(result)

        detect_changes(event, result)

        self.assertEqual(event, event_before)
        self.assertEqual(result, result_before)


if __name__ == "__main__":
    unittest.main()
