"""Tests for asu_event_classifier.classify_asu_event(). Pure logic, no I/O.
Stdlib unittest only.

Run with: python -m unittest test_asu_event_classifier -v
"""
import unittest

from asu_event_classifier import classify_asu_event


def _event(name, description=None):
    return {"name": name, "description": description}


class SubtypeTests(unittest.TestCase):
    def test_career_fair(self):
        self.assertEqual(classify_asu_event(_event("Career & Internship Fair")), "career_fair")

    def test_employer_event(self):
        self.assertEqual(classify_asu_event(_event("Discovery Day with Viasat")), "employer_event")

    def test_networking(self):
        self.assertEqual(classify_asu_event(_event("Thunderbird Networking Lab")), "networking")

    def test_info_session(self):
        self.assertEqual(classify_asu_event(_event("Get To Know The Disney College Program")), "info_session")

    def test_workshop(self):
        self.assertEqual(classify_asu_event(_event("Creating Your Technical Resume")), "workshop")

    def test_career_services(self):
        self.assertEqual(classify_asu_event(_event("Career Center Tabling")), "career_services")

    def test_unknown_is_other(self):
        self.assertEqual(classify_asu_event(_event("Peace Corps Prep at ASU")), "other")

    def test_employer_keyword_outranks_info_session_keyword(self):
        # both "recruiter" (employer_event) and "information session" (info_session) present
        self.assertEqual(
            classify_asu_event(_event("Intel recruiter information session")), "employer_event"
        )

    def test_description_evidence_is_used(self):
        result = classify_asu_event(_event("Spring Kickoff", description="Join us for a resume workshop."))
        self.assertEqual(result, "workshop")

    def test_office_hours_is_career_services(self):
        self.assertEqual(classify_asu_event(_event("AZ Game and Fish Liaison Office Hours")), "career_services")

    def test_case_insensitive(self):
        self.assertEqual(classify_asu_event(_event("CAREER FAIR 2026")), "career_fair")


if __name__ == "__main__":
    unittest.main()
