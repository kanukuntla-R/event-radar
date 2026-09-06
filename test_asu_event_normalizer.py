"""Tests for asu_event_normalizer.normalize_candidate(). Pure logic, no I/O.
Stdlib unittest only.

Run with: python -m unittest test_asu_event_normalizer -v
"""
import unittest

from asu_event_normalizer import (
    normalize_candidate, parse_date_range, parse_time_range, is_explicit_all_day,
    resolve_timed_event_date_range,
)


def _raw(**overrides):
    base = dict(
        name="FSE Workshop: Creating Your Technical Resume",
        description=None,
        date_text="Tuesday, September 8, 2026",
        time_text="10am - 11am MST",
        location_text="https://asu.zoom.us/j/83631808605",
        source_url="https://career.eoss.asu.edu/events/2026/09/08/fse-workshop/",
        registration_url=None,
    )
    base.update(overrides)
    return base


class WhitespaceAndEntityTests(unittest.TestCase):
    def test_whitespace_cleanup(self):
        candidate = normalize_candidate(_raw(name="  Some   Event  \n Title  "))
        self.assertEqual(candidate["name"], "Some Event Title")

    def test_html_entity_cleanup(self):
        candidate = normalize_candidate(_raw(name="Poly Career Advising Drop-In&#039;s"))
        self.assertEqual(candidate["name"], "Poly Career Advising Drop-In's")

    def test_description_cleanup(self):
        candidate = normalize_candidate(_raw(description="Learn how&nbsp;to write a résumé.  "))
        self.assertEqual(candidate["description"], "Learn how to write a résumé.")


class RecurringSeriesDateRangeTests(unittest.TestCase):
    """V0.9B.1 regression: ASU Career Services sometimes represents a
    recurring/multi-occurrence series (no structured recurrence data
    anywhere, confirmed against the source's own .ics export) as a wide
    "Date: first - last" range with one daily time window. That must never
    become a single continuous multi-week/month timed event."""

    def test_genuine_continuous_multi_day_event_is_unaffected(self):
        # "One-on-One Career Chats with Gray Media" - real 1-day-span event.
        end, _desc = resolve_timed_event_date_range("2026-09-09", "2026-09-10", "09:00", None)
        self.assertEqual(end, "2026-09-10")

    def test_single_day_timed_event_is_unaffected(self):
        end, _desc = resolve_timed_event_date_range("2026-09-08", "2026-09-08", "10:00", None)
        self.assertEqual(end, "2026-09-08")

    def test_poly_drop_in_series_real_fixture_drops_distant_end_date(self):
        candidate = normalize_candidate(_raw(
            name="Poly Career Advising Drop-In's",
            date_text="Wednesday, August 26, 2026 - Wednesday, December 2, 2026",
            time_text="12pm - 2pm MST",
            location_text="Poly, Technology Center, Suite 101",
            description="During drop-in career support hours, staff can help you.",
        ))
        self.assertEqual(candidate["start_date"], "2026-08-26")
        self.assertIsNone(candidate["end_date"])
        self.assertIn("recurring/multi-occurrence series", candidate["description"])
        self.assertIn("2026-12-02", candidate["description"])

    def test_disney_info_session_real_fixture_drops_distant_end_date(self):
        candidate = normalize_candidate(_raw(
            name="Get To Know The Disney College Program - Fall 2026",
            date_text="Tuesday, August 18, 2026 - Thursday, September 10, 2026",
            time_text="8am - 11am MST",
            location_text="Virtual",
            description="Come join Recruiters with Disney Programs.",
        ))
        self.assertEqual(candidate["start_date"], "2026-08-18")
        self.assertIsNone(candidate["end_date"])
        self.assertIn("2026-09-10", candidate["description"])

    def test_note_appended_even_with_no_prior_description(self):
        candidate = normalize_candidate(_raw(
            date_text="Monday, August 17, 2026 - Monday, December 21, 2026",
            time_text="12pm - 1pm MST", description=None,
        ))
        self.assertIsNone(candidate["end_date"])
        self.assertTrue(candidate["description"])

    def test_no_start_time_leaves_date_range_untouched(self):
        # this guard only applies to TIMED events - an all-day wide date
        # range is a different semantic, out of scope here.
        end, _desc = resolve_timed_event_date_range("2026-08-26", "2026-12-02", None, None)
        self.assertEqual(end, "2026-12-02")

    def test_exactly_at_threshold_is_kept(self):
        end, _desc = resolve_timed_event_date_range("2026-08-26", "2026-09-02", "12:00", None)
        self.assertEqual(end, "2026-09-02")

    def test_one_day_past_threshold_is_dropped(self):
        end, _desc = resolve_timed_event_date_range("2026-08-26", "2026-09-03", "12:00", None)
        self.assertIsNone(end)

    def test_missing_end_date_is_untouched(self):
        end, _desc = resolve_timed_event_date_range("2026-08-26", None, "12:00", None)
        self.assertIsNone(end)


class DateTimeNormalizationTests(unittest.TestCase):
    def test_single_day_date(self):
        start, end = parse_date_range("Tuesday, September 8, 2026")
        self.assertEqual((start, end), ("2026-09-08", "2026-09-08"))

    def test_date_range(self):
        start, end = parse_date_range("Monday, August 17, 2026 - Monday, December 21, 2026")
        self.assertEqual((start, end), ("2026-08-17", "2026-12-21"))

    def test_time_range(self):
        start, end = parse_time_range("12pm - 1pm MST")
        self.assertEqual((start, end), ("12:00", "13:00"))

    def test_time_range_with_minutes(self):
        start, end = parse_time_range("10am - 10:30am MST")
        self.assertEqual((start, end), ("10:00", "10:30"))

    def test_midnight_and_noon_edge_cases(self):
        self.assertEqual(parse_time_range("12am - 1am")[0], "00:00")
        self.assertEqual(parse_time_range("12pm - 1pm")[0], "12:00")

    def test_candidate_gets_arizona_timezone_when_timed(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["timezone"], "America/Phoenix")
        self.assertEqual(candidate["start_time"], "10:00")
        self.assertEqual(candidate["end_time"], "11:00")

    def test_no_timezone_when_no_time(self):
        candidate = normalize_candidate(_raw(time_text=None))
        self.assertIsNone(candidate["timezone"])
        self.assertIsNone(candidate["start_time"])

    def test_unparseable_time_does_not_invent_a_value(self):
        candidate = normalize_candidate(_raw(time_text="Times vary"))
        self.assertIsNone(candidate["start_time"])
        self.assertIsNone(candidate["end_time"])
        self.assertIsNone(candidate["timezone"])
        # date is still usable even though time wasn't parseable
        self.assertEqual(candidate["start_date"], "2026-09-08")


class AllDayRecognitionTests(unittest.TestCase):
    def test_explicit_all_day_recognized(self):
        self.assertTrue(is_explicit_all_day("All Day"))

    def test_explicit_all_dash_day_recognized(self):
        self.assertTrue(is_explicit_all_day("All-Day"))

    def test_normal_timed_text_is_not_all_day(self):
        self.assertFalse(is_explicit_all_day("10am - 11am MST"))

    def test_unparseable_non_all_day_text_is_not_all_day(self):
        self.assertFalse(is_explicit_all_day("Times vary"))

    def test_missing_time_text_is_not_all_day(self):
        self.assertFalse(is_explicit_all_day(None))

    def test_candidate_all_day_flag_true_for_explicit_all_day(self):
        candidate = normalize_candidate(_raw(time_text="All Day"))
        self.assertTrue(candidate["all_day"])
        self.assertIsNone(candidate["start_time"])

    def test_candidate_all_day_flag_false_for_normal_timed_event(self):
        candidate = normalize_candidate(_raw())
        self.assertFalse(candidate["all_day"])
        self.assertEqual(candidate["start_time"], "10:00")

    def test_candidate_all_day_flag_false_for_unparseable_time(self):
        candidate = normalize_candidate(_raw(time_text="Times vary"))
        self.assertFalse(candidate["all_day"])
        self.assertIsNone(candidate["start_time"])

    def test_candidate_all_day_flag_false_for_missing_time(self):
        candidate = normalize_candidate(_raw(time_text=None))
        self.assertFalse(candidate["all_day"])
        self.assertIsNone(candidate["start_time"])


class UrlNormalizationTests(unittest.TestCase):
    def test_source_url_normalized(self):
        candidate = normalize_candidate(_raw(source_url="HTTPS://Career.eoss.asu.edu/events/foo/"))
        self.assertEqual(candidate["source_url"], "https://career.eoss.asu.edu/events/foo")

    def test_registration_url_normalized_and_sets_open(self):
        candidate = normalize_candidate(_raw(registration_url="HTTPS://Career-asu.12twenty.com/Events#/x"))
        # fragment is preserved - it's the only thing identifying the event
        # on this SPA-routed registration portal.
        self.assertEqual(candidate["registration_url"], "https://career-asu.12twenty.com/Events#/x")
        self.assertEqual(candidate["registration_status"], "open")

    def test_registration_url_fragment_distinguishes_different_events(self):
        a = normalize_candidate(_raw(registration_url="https://career-asu.12twenty.com/Events#/events/111"))
        b = normalize_candidate(_raw(registration_url="https://career-asu.12twenty.com/Events#/events/222"))
        self.assertNotEqual(a["registration_url"], b["registration_url"])

    def test_no_registration_url_stays_unknown(self):
        candidate = normalize_candidate(_raw(registration_url=None))
        self.assertIsNone(candidate["registration_url"])
        self.assertEqual(candidate["registration_status"], "unknown")


class MissingEvidenceTests(unittest.TestCase):
    def test_missing_date_text_stays_none(self):
        candidate = normalize_candidate(_raw(date_text=None))
        self.assertIsNone(candidate["start_date"])
        self.assertIsNone(candidate["end_date"])
        self.assertIsNone(candidate["date_status"])

    def test_missing_location_stays_none(self):
        candidate = normalize_candidate(_raw(location_text=None))
        self.assertIsNone(candidate["location"])
        self.assertIsNone(candidate["location_type"])
        self.assertIsNone(candidate["location_area"])


class LocationAndSubtypeIntegrationTests(unittest.TestCase):
    def test_location_classification_applied(self):
        candidate = normalize_candidate(_raw(location_text="Tempe, ECG, North Patio"))
        self.assertEqual(candidate["location_area"], "tempe")
        self.assertEqual(candidate["location_type"], "in_person")

    def test_subtype_classification_applied(self):
        candidate = normalize_candidate(_raw(name="Creating Your Technical Resume"))
        self.assertEqual(candidate["event_subtype"], "workshop")

    def test_fixed_source_and_organizer(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["source"], "ASU Career Services")
        self.assertEqual(candidate["organizer"], "ASU Career Services")


if __name__ == "__main__":
    unittest.main()
