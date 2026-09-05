"""Standalone tests for hackathon_strategy.evaluate(). Stdlib unittest only.

Run with: python -m unittest test_hackathon_strategy -v
"""
import copy
import unittest
from datetime import date

from hackathon_strategy import evaluate

TODAY = date(2026, 9, 4)


def _event(**overrides):
    base = dict(
        date_status="confirmed",
        start_date=None,
        end_date=None,
        registration_status=None,
        last_checked=None,
        expected_start_date=None,
    )
    base.update(overrides)
    return base


class HackathonStrategyTests(unittest.TestCase):
    # A. TBA + never checked
    def test_tba_never_checked(self):
        event = _event(date_status="tba")
        result = evaluate(event, TODAY)
        self.assertEqual(
            result, {"should_check": True, "reason": "date_tba_never_checked", "next_check": None}
        )

    # B. TBA + checked yesterday (default 14-day cadence, not due)
    def test_tba_checked_yesterday(self):
        event = _event(date_status="tba", last_checked="2026-09-03")
        result = evaluate(event, TODAY)
        self.assertEqual(result["should_check"], False)
        self.assertEqual(result["reason"], "checked_recently")
        self.assertEqual(result["next_check"], date(2026, 9, 17))  # 2026-09-03 + 14 days

    # C. TBA + due after cadence
    def test_tba_due_after_default_cadence(self):
        event = _event(date_status="tba", last_checked="2026-08-15")  # 20 days ago
        result = evaluate(event, TODAY)
        self.assertEqual(
            result, {"should_check": True, "reason": "tba_check_due", "next_check": None}
        )

    def test_tba_cadence_scales_with_expected_start_date(self):
        # last_checked 2026-08-15 -> 20 days elapsed by TODAY for all three
        far = _event(date_status="tba", last_checked="2026-08-15", expected_start_date="2027-06-01")  # ~270 days away -> 30-day cadence
        mid = _event(date_status="tba", last_checked="2026-08-15", expected_start_date="2026-12-20")  # ~107 days away -> 14-day cadence
        near = _event(date_status="tba", last_checked="2026-08-15", expected_start_date="2026-09-25")  # ~21 days away -> 7-day cadence

        # 20 days elapsed: due under the 14/7-day cadences, NOT due under the 30-day one
        self.assertFalse(evaluate(far, TODAY)["should_check"])
        self.assertTrue(evaluate(mid, TODAY)["should_check"])
        self.assertTrue(evaluate(near, TODAY)["should_check"])

    # D. confirmed + registration unknown
    def test_confirmed_registration_unknown_due(self):
        event = _event(
            date_status="confirmed", registration_status="unknown",
            start_date="2026-12-01", end_date="2026-12-02", last_checked="2026-08-25",  # 10 days ago
        )
        result = evaluate(event, TODAY)
        self.assertEqual(
            result, {"should_check": True, "reason": "registration_unknown_check_due", "next_check": None}
        )

    def test_confirmed_registration_unknown_never_checked(self):
        event = _event(
            date_status="confirmed", registration_status="unknown",
            start_date="2026-12-01", end_date="2026-12-02",
        )
        result = evaluate(event, TODAY)
        self.assertEqual(
            result,
            {"should_check": True, "reason": "registration_unknown_never_checked", "next_check": None},
        )

    # E. registration open
    def test_registration_open_due(self):
        event = _event(
            date_status="confirmed", registration_status="open",
            start_date="2026-12-01", end_date="2026-12-02", last_checked="2026-08-25",  # 10 days ago
        )
        result = evaluate(event, TODAY)
        self.assertEqual(
            result, {"should_check": True, "reason": "registration_open_check_due", "next_check": None}
        )

    # F. registration closed
    def test_registration_closed_due(self):
        event = _event(
            date_status="confirmed", registration_status="closed",
            start_date="2026-12-01", end_date="2026-12-02", last_checked="2026-08-20",  # 15 days ago
        )
        result = evaluate(event, TODAY)
        self.assertEqual(
            result, {"should_check": True, "reason": "registration_closed_check_due", "next_check": None}
        )

    def test_registration_closed_not_due(self):
        event = _event(
            date_status="confirmed", registration_status="closed",
            start_date="2026-12-01", end_date="2026-12-02", last_checked="2026-08-30",  # 5 days ago
        )
        result = evaluate(event, TODAY)
        self.assertEqual(result["should_check"], False)
        self.assertEqual(result["reason"], "checked_recently")
        self.assertEqual(result["next_check"], date(2026, 9, 13))  # 2026-08-30 + 14 days

    # G. past events - finished overrides everything, even never-checked
    def test_finished_event_never_checked(self):
        event = _event(date_status="confirmed", start_date="2026-01-01", end_date="2026-01-02")
        result = evaluate(event, TODAY)
        self.assertEqual(
            result, {"should_check": False, "reason": "event_finished", "next_check": None}
        )

    def test_finished_event_with_last_checked(self):
        event = _event(
            date_status="confirmed", start_date="2026-01-01", end_date="2026-01-02",
            registration_status="open", last_checked="2026-08-30",
        )
        result = evaluate(event, TODAY)
        self.assertEqual(
            result, {"should_check": False, "reason": "event_finished", "next_check": None}
        )

    def test_finished_single_day_event_falls_back_to_start_date(self):
        # end_date is null but start_date alone already places it in the past
        event = _event(date_status="confirmed", start_date="2026-01-01", end_date=None)
        result = evaluate(event, TODAY)
        self.assertEqual(result["reason"], "event_finished")

    # H. exact cadence boundary (confirmed + unknown -> 7-day cadence)
    def test_cadence_boundary_exactly_due(self):
        event = _event(
            date_status="confirmed", registration_status="unknown",
            start_date="2026-12-01", end_date="2026-12-02", last_checked="2026-08-28",  # exactly 7 days ago
        )
        result = evaluate(event, TODAY)
        self.assertTrue(result["should_check"])
        self.assertEqual(result["reason"], "registration_unknown_check_due")

    def test_cadence_boundary_one_day_short(self):
        event = _event(
            date_status="confirmed", registration_status="unknown",
            start_date="2026-12-01", end_date="2026-12-02", last_checked="2026-08-29",  # 6 days ago
        )
        result = evaluate(event, TODAY)
        self.assertFalse(result["should_check"])
        self.assertEqual(result["reason"], "checked_recently")
        self.assertEqual(result["next_check"], date(2026, 9, 5))  # 2026-08-29 + 7 days

    # Purity checks
    def test_does_not_mutate_event(self):
        event = _event(
            date_status="confirmed", registration_status="open",
            start_date="2026-12-01", end_date="2026-12-02", last_checked="2026-08-25",
        )
        before = copy.deepcopy(event)
        evaluate(event, TODAY)
        self.assertEqual(event, before)

    def test_handles_sqlite_datetime_string(self):
        event = _event(date_status="tba", last_checked="2026-08-15 13:45:02")
        result = evaluate(event, TODAY)
        self.assertTrue(result["should_check"])
        self.assertEqual(result["reason"], "tba_check_due")


if __name__ == "__main__":
    unittest.main()
