"""Tests for event_location.classify_location(). Pure logic, no I/O.
Stdlib unittest only.

Run with: python -m unittest test_event_location -v
"""
import unittest

from event_location import classify_location


class KnownAreaTests(unittest.TestCase):
    def test_tempe_address(self):
        result = classify_location("Tempe, ECG, North Patio")
        self.assertEqual(result, {"location_type": "in_person", "location_area": "tempe", "priority": "primary"})

    def test_tempe_street_address(self):
        result = classify_location("2040 E Technology Cir, Tempe, AZ 85284")
        self.assertEqual(result["location_area"], "tempe")

    def test_scottsdale(self):
        result = classify_location("SkySong, 1475 N Scottsdale Rd, Scottsdale, AZ")
        self.assertEqual(result["location_area"], "scottsdale")
        self.assertEqual(result["priority"], "primary")

    def test_chandler(self):
        result = classify_location("SRP Innovation Center, Chandler, AZ")
        self.assertEqual(result["location_area"], "chandler")
        self.assertEqual(result["priority"], "primary")

    def test_phoenix(self):
        result = classify_location("Downtown Phoenix campus, Phoenix, AZ")
        self.assertEqual(result["location_area"], "phoenix")
        self.assertEqual(result["priority"], "nearby")

    def test_mesa_via_poly(self):
        result = classify_location("Poly, Technology Center, Suite 101")
        self.assertEqual(result["location_area"], "mesa")
        self.assertEqual(result["priority"], "nearby")

    def test_mesa_direct(self):
        result = classify_location("Mesa, AZ")
        self.assertEqual(result["location_area"], "mesa")


class OnlineTests(unittest.TestCase):
    def test_virtual(self):
        result = classify_location("Virtual")
        self.assertEqual(result, {"location_type": "online", "location_area": "online", "priority": "primary"})

    def test_zoom_url(self):
        result = classify_location("https://asu.zoom.us/j/83631808605")
        self.assertEqual(result["location_type"], "online")
        self.assertEqual(result["location_area"], "online")

    def test_hybrid(self):
        result = classify_location("Hybrid - Tempe or Zoom")
        self.assertEqual(result["location_type"], "hybrid")
        self.assertEqual(result["location_area"], "hybrid")


class UnknownTests(unittest.TestCase):
    def test_unfamiliar_building_is_other_not_a_guess(self):
        result = classify_location("Life Sciences Center: Room 404")
        self.assertEqual(result["location_area"], "other")
        self.assertEqual(result["location_type"], "in_person")

    def test_does_not_guess_city_from_unfamiliar_building_name(self):
        result = classify_location("Thunderbird School of Global Management")
        self.assertEqual(result["location_area"], "other")

    def test_named_city_in_building_name_is_used(self):
        result = classify_location("Thunderbird School of Global Management, Phoenix, AZ")
        self.assertEqual(result["location_area"], "phoenix")


class EmptyInputTests(unittest.TestCase):
    def test_none_returns_all_none(self):
        result = classify_location(None)
        self.assertEqual(result, {"location_type": None, "location_area": None, "priority": None})

    def test_empty_string_returns_all_none(self):
        result = classify_location("   ")
        self.assertEqual(result, {"location_type": None, "location_area": None, "priority": None})


if __name__ == "__main__":
    unittest.main()
