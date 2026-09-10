"""Tests for az_tech_council_normalizer.normalize_candidate(). Pure logic,
no I/O. Stdlib unittest only.

Run with: python -m unittest test_az_tech_council_normalizer -v
"""
import unittest

from az_tech_council_normalizer import normalize_candidate, SOURCE_NAME, EVENT_TYPE


def _raw(**overrides):
    base = dict(
        name="TiE Phoenix Monthly Event",
        description="Join us for networking.",
        date_text="October 16, 2026",
        time_text="8:30 AM - 12:30PM",
        location_text="McCord Hall | ASU Tempe campus",
        source_url="https://www.aztechcouncil.org/event/tie-phoenix/",
        registration_url=None,
        organizer=None,
        categories=None,
    )
    base.update(overrides)
    return base


class WhitespaceAndEntityTests(unittest.TestCase):
    def test_html_entity_cleanup(self):
        candidate = normalize_candidate(_raw(name="Networking &amp; Mixer"))
        self.assertNotIn("&amp;", candidate["name"])

    def test_whitespace_cleanup(self):
        candidate = normalize_candidate(_raw(description="Join us&nbsp; for  networking."))
        self.assertNotIn("&nbsp;", candidate["description"])
        self.assertNotIn("  ", candidate["description"])


class DateTimeTests(unittest.TestCase):
    def test_date_and_time_normalized(self):
        candidate = normalize_candidate(_raw(date_text="October 16, 2026", time_text="8:30 AM - 12:30PM"))
        self.assertEqual(candidate["start_date"], "2026-10-16")
        self.assertEqual(candidate["start_time"], "08:30")
        self.assertEqual(candidate["end_time"], "12:30")

    def test_lowercase_no_space_time_parses(self):
        candidate = normalize_candidate(_raw(time_text="3:30pm - 5:00pm"))
        self.assertEqual(candidate["start_time"], "15:30")
        self.assertEqual(candidate["end_time"], "17:00")

    def test_single_day_end_date_defaults_to_start(self):
        candidate = normalize_candidate(_raw(date_text="October 16, 2026"))
        self.assertEqual(candidate["end_date"], "2026-10-16")

    def test_unparseable_date_stays_none(self):
        candidate = normalize_candidate(_raw(date_text="Sometime next month"))
        self.assertIsNone(candidate["start_date"])
        self.assertIsNone(candidate["end_date"])

    def test_missing_time_stays_none_not_all_day(self):
        candidate = normalize_candidate(_raw(time_text=None))
        self.assertIsNone(candidate["start_time"])
        self.assertFalse(candidate["all_day"])

    def test_timezone_defaults_to_arizona_when_time_present(self):
        candidate = normalize_candidate(_raw(time_text="8:30 AM - 12:30PM"))
        self.assertEqual(candidate["timezone"], "America/Phoenix")

    def test_timezone_none_when_no_time(self):
        candidate = normalize_candidate(_raw(time_text=None))
        self.assertIsNone(candidate["timezone"])

    def test_date_status_confirmed_when_date_present(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["date_status"], "confirmed")

    def test_date_status_none_when_no_date(self):
        candidate = normalize_candidate(_raw(date_text=None))
        self.assertIsNone(candidate["date_status"])


class LocationTests(unittest.TestCase):
    def test_in_person_tempe_classified(self):
        candidate = normalize_candidate(_raw(location_text="McCord Hall | ASU Tempe campus"))
        self.assertEqual(candidate["location_type"], "in_person")
        self.assertEqual(candidate["location_area"], "tempe")

    def test_virtual_event_classified_online(self):
        candidate = normalize_candidate(_raw(location_text="Virtual Event, Register to Attend"))
        self.assertEqual(candidate["location_type"], "online")

    def test_tucson_location_classified_other_not_fabricated_area(self):
        # event_location.classify_location has no Tucson-area keyword -
        # must land as "other", never a guessed/fabricated area.
        candidate = normalize_candidate(_raw(location_text="Tucson Convention Center - Grand Ballroom"))
        self.assertEqual(candidate["location_type"], "in_person")
        self.assertEqual(candidate["location_area"], "other")

    def test_no_location_text_no_fabricated_classification(self):
        candidate = normalize_candidate(_raw(location_text=None))
        self.assertIsNone(candidate["location_type"])
        self.assertIsNone(candidate["location_area"])


class SourceIdentityTests(unittest.TestCase):
    def test_source_is_stable_canonical_name(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["source"], "Arizona Technology Council")
        self.assertEqual(candidate["source"], SOURCE_NAME)

    def test_event_type_is_professional_not_asu_default(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["event_type"], "professional")
        self.assertEqual(candidate["event_type"], EVENT_TYPE)

    def test_event_subtype_not_fabricated(self):
        candidate = normalize_candidate(_raw())
        self.assertIsNone(candidate["event_subtype"])

    def test_organizer_defaults_to_source_name(self):
        candidate = normalize_candidate(_raw(organizer=None))
        self.assertEqual(candidate["organizer"], "Arizona Technology Council")

    def test_presented_by_used_as_organizer_when_given(self):
        candidate = normalize_candidate(_raw(organizer="ASU W.P. Carey School of Business"))
        self.assertEqual(candidate["organizer"], "ASU W.P. Carey School of Business")


class RegistrationUrlTests(unittest.TestCase):
    def test_distinct_registration_url_kept(self):
        candidate = normalize_candidate(_raw(
            source_url="https://www.aztechcouncil.org/event/foo/",
            registration_url="https://specialevents.asu.edu/ereg/newreg.php?eventid=1",
        ))
        self.assertEqual(candidate["registration_url"], "https://specialevents.asu.edu/ereg/newreg.php?eventid=1")
        self.assertEqual(candidate["registration_status"], "open")

    def test_registration_url_same_as_source_is_dropped(self):
        candidate = normalize_candidate(_raw(
            source_url="https://www.aztechcouncil.org/event/foo/",
            registration_url="https://www.aztechcouncil.org/event/foo/",
        ))
        self.assertIsNone(candidate["registration_url"])
        self.assertEqual(candidate["registration_status"], "unknown")

    def test_no_registration_url_status_unknown(self):
        candidate = normalize_candidate(_raw(registration_url=None))
        self.assertIsNone(candidate["registration_url"])
        self.assertEqual(candidate["registration_status"], "unknown")


class CategoriesTests(unittest.TestCase):
    def test_categories_cleaned_and_passed_through(self):
        candidate = normalize_candidate(_raw(categories=["community-tech-events", "virtual-aztc-events"]))
        self.assertEqual(candidate["categories"], ["community-tech-events", "virtual-aztc-events"])

    def test_missing_categories_is_empty_list(self):
        candidate = normalize_candidate(_raw(categories=None))
        self.assertEqual(candidate["categories"], [])


if __name__ == "__main__":
    unittest.main()
