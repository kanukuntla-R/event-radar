"""Tests for source_health.classify_source_health(). Pure logic, no SQLite,
no HTTP. Stdlib unittest only.

Run with: python -m unittest test_source_health -v
"""
import unittest

from source_health import classify_source_health


def _event(**overrides):
    base = dict(
        event_state="active", consecutive_failures=0,
        last_check_status=None, last_successful_check=None,
    )
    base.update(overrides)
    return base


class FailureCountTests(unittest.TestCase):
    def test_zero_failures_is_healthy(self):
        result = classify_source_health(_event(consecutive_failures=0, last_check_status="ok"))
        self.assertEqual(result["level"], "healthy")
        self.assertFalse(result["needs_review"])

    def test_one_failure_is_warning(self):
        result = classify_source_health(_event(consecutive_failures=1, last_check_status="unavailable"))
        self.assertEqual(result["level"], "warning")
        self.assertFalse(result["needs_review"])

    def test_two_failures_is_repeated_failure(self):
        result = classify_source_health(_event(consecutive_failures=2, last_check_status="not_found"))
        self.assertEqual(result["level"], "repeated_failure")
        self.assertFalse(result["needs_review"])

    def test_three_failures_is_review(self):
        result = classify_source_health(_event(consecutive_failures=3, last_check_status="unavailable"))
        self.assertEqual(result["level"], "review")
        self.assertTrue(result["needs_review"])

    def test_five_failures_is_review(self):
        result = classify_source_health(_event(consecutive_failures=5, last_check_status="parse_failed"))
        self.assertEqual(result["level"], "review")
        self.assertTrue(result["needs_review"])

    def test_needs_review_only_true_at_threshold(self):
        for n in (0, 1, 2):
            self.assertFalse(classify_source_health(_event(consecutive_failures=n))["needs_review"], n)
        for n in (3, 4, 10):
            self.assertTrue(classify_source_health(_event(consecutive_failures=n))["needs_review"], n)


class InactiveEventTests(unittest.TestCase):
    def test_inactive_event_classified_distinctly(self):
        result = classify_source_health(_event(event_state="inactive", consecutive_failures=5))
        self.assertEqual(result["level"], "inactive")
        self.assertFalse(result["needs_review"])

    def test_inactive_event_with_zero_failures_still_inactive(self):
        result = classify_source_health(_event(event_state="inactive", consecutive_failures=0))
        self.assertEqual(result["level"], "inactive")


class MissingDataTests(unittest.TestCase):
    def test_missing_consecutive_failures_defaults_to_healthy(self):
        event = {"event_state": "active"}
        result = classify_source_health(event)
        self.assertEqual(result["level"], "healthy")
        self.assertEqual(result["consecutive_failures"], 0)

    def test_null_last_check_status_handled_cleanly(self):
        result = classify_source_health(_event(consecutive_failures=1, last_check_status=None))
        self.assertEqual(result["level"], "warning")
        self.assertIn("unknown", result["message"])

    def test_missing_event_state_defaults_to_active(self):
        event = {"consecutive_failures": 1, "last_check_status": "unavailable"}
        result = classify_source_health(event)
        self.assertEqual(result["level"], "warning")

    def test_does_not_mutate_event(self):
        event = _event(consecutive_failures=2)
        before = dict(event)
        classify_source_health(event)
        self.assertEqual(event, before)


if __name__ == "__main__":
    unittest.main()
