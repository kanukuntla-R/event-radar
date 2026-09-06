"""Tests for asu_events_normalizer.normalize_candidate(). Pure logic, no
I/O. Stdlib unittest only.

Run with: python -m unittest test_asu_events_normalizer -v
"""
import unittest

from asu_events_normalizer import normalize_candidate


def _raw(**overrides):
    base = dict(
        name="Research Computing Open Office Hours",
        description=None,
        date_text="Tue, Sep 8, 2026",
        time_text="10:00 am &ndash; 11:00 am (MST)",
        location_text="Zoom",
        source_url="https://asuevents.asu.edu/event/research-computing-open-office-hours?eventDate=2026-09-08&id=2",
        registration_url=None,
        categories=None,
        audiences=None,
        organizer=None,
    )
    base.update(overrides)
    return base


class WhitespaceAndEntityTests(unittest.TestCase):
    def test_html_entity_cleanup(self):
        candidate = normalize_candidate(_raw(name="Research for Public Impact: Engaging&hellip;"))
        self.assertNotIn("&hellip;", candidate["name"])

    def test_whitespace_cleanup(self):
        candidate = normalize_candidate(_raw(description="Meet our experts during&nbsp; virtual &nbsp;office hours."))
        self.assertNotIn("&nbsp;", candidate["description"])
        self.assertNotIn("  ", candidate["description"])


class DateTimeTests(unittest.TestCase):
    def test_date_and_time_normalized(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["start_date"], "2026-09-08")
        self.assertEqual(candidate["start_time"], "10:00")
        self.assertEqual(candidate["end_time"], "11:00")

    def test_en_dash_time_separator_parses(self):
        candidate = normalize_candidate(_raw(time_text="1:00 pm &ndash; 3:30 pm (MST)"))
        self.assertEqual(candidate["start_time"], "13:00")
        self.assertEqual(candidate["end_time"], "15:30")

    def test_arizona_timezone_when_timed(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["timezone"], "America/Phoenix")

    def test_explicit_all_day_recognized(self):
        candidate = normalize_candidate(_raw(time_text="All Day"))
        self.assertTrue(candidate["all_day"])
        self.assertIsNone(candidate["start_time"])

    def test_wide_timed_date_range_is_preserved_not_truncated(self):
        # V0.9B.2: the "first date - last date + one daily time window
        # means a recurring series" heuristic is proven ONLY for ASU
        # Career Services (career.eoss.asu.edu / uConnect), via that
        # platform's own .ics export. ASU Events (asuevents.asu.edu /
        # Drupal) is a different source/platform with no such proof - a
        # legitimate long-running timed program here must keep its real
        # end_date rather than being silently truncated to one day.
        candidate = normalize_candidate(_raw(
            date_text="Wed, Aug 26, 2026 - Wed, Dec 2, 2026", time_text="12:00 pm &ndash; 2:00 pm (MST)",
        ))
        self.assertEqual(candidate["start_date"], "2026-08-26")
        self.assertEqual(candidate["end_date"], "2026-12-02")
        self.assertIsNone(candidate["description"])

    def test_genuine_short_multi_day_event_unaffected(self):
        candidate = normalize_candidate(_raw(date_text="Wed, Sep 9, 2026 - Thu, Sep 10, 2026"))
        self.assertEqual(candidate["start_date"], "2026-09-09")
        self.assertEqual(candidate["end_date"], "2026-09-10")

    def test_unparseable_time_rejected_downstream_not_treated_as_all_day(self):
        candidate = normalize_candidate(_raw(time_text="Times vary"))
        self.assertFalse(candidate["all_day"])
        self.assertIsNone(candidate["start_time"])
        # event_discovery_store.validate_candidate() is what actually
        # rejects this - the normalizer just refuses to fabricate a value.


class LocationMappingTests(unittest.TestCase):
    def test_zoom_maps_to_online(self):
        candidate = normalize_candidate(_raw(location_text="Zoom"))
        self.assertEqual(candidate["location_area"], "online")

    def test_tempe_campus_maps_to_tempe(self):
        candidate = normalize_candidate(_raw(location_text="Tempe campus"))
        self.assertEqual(candidate["location_area"], "tempe")

    def test_skysong_maps_to_scottsdale(self):
        candidate = normalize_candidate(_raw(location_text="SkySong, the ASU Scottsdale Innovation Center"))
        self.assertEqual(candidate["location_area"], "scottsdale")

    def test_downtown_phoenix_maps_to_phoenix(self):
        candidate = normalize_candidate(_raw(location_text="Downtown Phoenix campus"))
        self.assertEqual(candidate["location_area"], "phoenix")

    def test_mesa_city_center_maps_to_mesa(self):
        candidate = normalize_candidate(_raw(location_text="ASU at Mesa City Center"))
        self.assertEqual(candidate["location_area"], "mesa")

    def test_polytechnic_campus_maps_to_mesa(self):
        candidate = normalize_candidate(_raw(location_text="Polytechnic campus"))
        self.assertEqual(candidate["location_area"], "mesa")

    def test_online_event_maps_to_online(self):
        candidate = normalize_candidate(_raw(location_text="Online event"))
        self.assertEqual(candidate["location_area"], "online")


class CategoryAudienceEvidenceTests(unittest.TestCase):
    def test_categories_captured(self):
        candidate = normalize_candidate(_raw(categories=["Professional and career development", "Science"]))
        self.assertEqual(candidate["categories"], ["Professional and career development", "Science"])

    def test_audiences_captured(self):
        candidate = normalize_candidate(_raw(audiences=["Current ASU students"]))
        self.assertEqual(candidate["audiences"], ["Current ASU students"])

    def test_missing_categories_and_audiences_are_empty_lists(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["categories"], [])
        self.assertEqual(candidate["audiences"], [])


class SourceIdentityTests(unittest.TestCase):
    def test_source_is_asu_events(self):
        candidate = normalize_candidate(_raw())
        self.assertEqual(candidate["source"], "ASU Events")

    def test_event_type_left_for_store_to_default(self):
        # source-identity fields set by the normalizer never include
        # event_type - that's a store-level default (event_type='asu').
        candidate = normalize_candidate(_raw())
        self.assertNotIn("event_type", candidate)


if __name__ == "__main__":
    unittest.main()
