"""Tests for calendar_service.event_effective_end() / is_event_past() - the
shared, timezone-aware "when does this event end" logic used by both
generate_ics() (unchanged behavior, see test_calendar_routes.py/test_app.py)
and the dashboard's default hide-past-events filter (app.py).

Deterministic: every is_event_past() call here passes an explicit `now`, so
nothing depends on the actual wall clock. Stdlib unittest only.

Run with: python -m unittest test_calendar_service_visibility -v
"""
import unittest
from datetime import datetime, timezone as dt_timezone

from calendar_service import is_event_past, event_effective_end


def _event(**overrides):
    fields = {
        "start_date": None, "end_date": None, "start_time": None, "end_time": None,
        "timezone": None,
    }
    fields.update(overrides)
    return fields


def _utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=dt_timezone.utc)


class TimedEventTests(unittest.TestCase):
    def test_past_timed_event_is_hidden(self):
        e = _event(start_date="2026-09-20", start_time="10:00", end_time="11:00", timezone="America/Phoenix")
        now = _utc(2026, 9, 20, 19, 0)  # well after 11:00 MST (18:00 UTC)
        self.assertTrue(is_event_past(e, now=now))

    def test_future_timed_event_is_visible(self):
        e = _event(start_date="2026-12-25", start_time="10:00", end_time="11:00", timezone="America/Phoenix")
        now = _utc(2026, 9, 20, 12, 0)
        self.assertFalse(is_event_past(e, now=now))

    def test_currently_running_timed_event_is_visible(self):
        # 10:00-14:00 MST (17:00-21:00 UTC); "now" is 18:00 UTC - mid-event.
        e = _event(start_date="2026-09-20", start_time="10:00", end_time="14:00", timezone="America/Phoenix")
        now = _utc(2026, 9, 20, 18, 0)
        self.assertFalse(is_event_past(e, now=now))

    def test_start_time_passed_but_no_end_time_uses_default_duration(self):
        # No end_time -> DEFAULT_DURATION (1h) from start. Started 10:00 MST
        # (17:00 UTC); "now" is 17:30 UTC - still within the default hour.
        e = _event(start_date="2026-09-20", start_time="10:00", timezone="America/Phoenix")
        now = _utc(2026, 9, 20, 17, 30)
        self.assertFalse(is_event_past(e, now=now))
        later = _utc(2026, 9, 20, 18, 1)  # just past the 1h default duration
        self.assertTrue(is_event_past(e, now=later))


class MultiDayAndAllDayTests(unittest.TestCase):
    def test_multiday_timed_event_visible_until_actual_end(self):
        e = _event(
            start_date="2026-09-20", start_time="09:00",
            end_date="2026-09-22", end_time="17:00", timezone="America/Phoenix",
        )
        still_running = _utc(2026, 9, 21, 12, 0)  # day 2 of 3, well before end
        self.assertFalse(is_event_past(e, now=still_running))
        after_end = _utc(2026, 9, 23, 1, 0)  # after 2026-09-22 17:00 MST (2026-09-23 00:00 UTC)
        self.assertTrue(is_event_past(e, now=after_end))

    def test_expired_multiday_event_hidden(self):
        e = _event(
            start_date="2026-09-10", start_time="09:00",
            end_date="2026-09-12", end_time="17:00", timezone="America/Phoenix",
        )
        now = _utc(2026, 9, 20, 0, 0)
        self.assertTrue(is_event_past(e, now=now))

    def test_all_day_event_visible_through_its_final_date(self):
        e = _event(start_date="2026-09-20", end_date="2026-09-22", timezone="America/Phoenix")
        # Still 2026-09-22 in Phoenix (MST = UTC-7) at 2026-09-22 23:00 UTC (16:00 local).
        still_final_day = _utc(2026, 9, 22, 23, 0)
        self.assertFalse(is_event_past(e, now=still_final_day))

    def test_expired_all_day_event_hidden(self):
        e = _event(start_date="2026-09-20", end_date="2026-09-22", timezone="America/Phoenix")
        # 2026-09-23 07:01 UTC = 2026-09-23 00:01 MST - the day after end_date has begun.
        now = _utc(2026, 9, 23, 7, 1)
        self.assertTrue(is_event_past(e, now=now))

    def test_single_day_all_day_event_visible_all_day(self):
        e = _event(start_date="2026-09-20", timezone="America/Phoenix")
        evening_local = _utc(2026, 9, 20, 23, 0)  # 16:00 MST, still the event's own date
        self.assertFalse(is_event_past(e, now=evening_local))
        next_day = _utc(2026, 9, 21, 7, 1)  # 00:01 MST the next day
        self.assertTrue(is_event_past(e, now=next_day))


class TimezoneTests(unittest.TestCase):
    def test_non_phoenix_timezone_used_correctly(self):
        # 20:00 America/New_York (EST, UTC-5) = 01:00 UTC the next day.
        e = _event(start_date="2026-12-01", start_time="20:00", end_time="22:00", timezone="America/New_York")
        during = _utc(2026, 12, 2, 1, 30)  # 20:30 EST - mid-event
        self.assertFalse(is_event_past(e, now=during))
        after = _utc(2026, 12, 2, 3, 1)  # 22:01 EST - just after it ends
        self.assertTrue(is_event_past(e, now=after))

    def test_missing_timezone_falls_back_to_phoenix_default(self):
        e = _event(start_date="2026-09-20", start_time="10:00", end_time="11:00", timezone=None)
        # 11:00 MST = 18:00 UTC.
        during = _utc(2026, 9, 20, 17, 59)
        self.assertFalse(is_event_past(e, now=during))
        after = _utc(2026, 9, 20, 18, 1)
        self.assertTrue(is_event_past(e, now=after))

    def test_unknown_timezone_name_falls_back_to_phoenix_default(self):
        e = _event(start_date="2026-09-20", start_time="10:00", end_time="11:00", timezone="Not/A_Real_Zone")
        after = _utc(2026, 9, 20, 18, 1)
        self.assertTrue(is_event_past(e, now=after))


class UncertainDateTests(unittest.TestCase):
    def test_missing_start_date_is_never_past(self):
        e = _event(start_date=None)
        self.assertFalse(is_event_past(e, now=_utc(2099, 1, 1)))
        self.assertIsNone(event_effective_end(e))

    def test_unparseable_start_date_is_never_past(self):
        e = _event(start_date="TBA")
        self.assertFalse(is_event_past(e, now=_utc(2099, 1, 1)))
        self.assertIsNone(event_effective_end(e))

    def test_malformed_end_time_treated_as_uncertain_not_past(self):
        # event_effective_end() raises ValueError here (same as the
        # pre-existing calendar behavior this was extracted from);
        # is_event_past() must catch that and treat it as "not past".
        e = _event(start_date="2026-09-20", start_time="10:00", end_time="not-a-time", timezone="America/Phoenix")
        self.assertFalse(is_event_past(e, now=_utc(2099, 1, 1)))


class NowDefaultTests(unittest.TestCase):
    def test_now_defaults_to_current_time_when_omitted(self):
        far_future = _event(start_date="2099-01-01", timezone="America/Phoenix")
        self.assertFalse(is_event_past(far_future))
        far_past = _event(start_date="2000-01-01", timezone="America/Phoenix")
        self.assertTrue(is_event_past(far_past))


if __name__ == "__main__":
    unittest.main()
