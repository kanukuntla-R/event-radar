"""Tests for asu_event_relevance.classify_relevance(). Pure logic, no I/O.
Stdlib unittest only.

Run with: python -m unittest test_asu_event_relevance -v
"""
import unittest

from asu_event_relevance import classify_relevance


def _event(name="", description=None, categories=None, audiences=None):
    return {"name": name, "description": description, "categories": categories, "audiences": audiences}


class IncludeTests(unittest.TestCase):
    def test_career_fair_includes(self):
        result = classify_relevance(_event("Fall 2026 Career & Internship Fair"))
        self.assertEqual(result["decision"], "include")

    def test_recruiter_employer_includes(self):
        result = classify_relevance(_event("Discovery Day with Viasat", description="Meet our recruiters."))
        self.assertEqual(result["decision"], "include")

    def test_networking_includes(self):
        result = classify_relevance(_event("Thunderbird Networking Lab"))
        self.assertEqual(result["decision"], "include")

    def test_strong_technical_event_includes(self):
        result = classify_relevance(_event(
            "OpenUSD + NVIDIA Omniverse: Building the Future of 3D Worlds",
            description="Explore how OpenUSD and NVIDIA Omniverse are changing 3D design and physical AI.",
            categories=["Academic events"],
        ))
        self.assertEqual(result["decision"], "include")

    def test_professional_and_career_development_category_includes(self):
        result = classify_relevance(_event("Research Computing Open Office Hours", categories=["Professional and career development"]))
        self.assertEqual(result["decision"], "include")


class ReviewOnlyEvidenceTests(unittest.TestCase):
    def test_science_alone_does_not_automatically_include(self):
        result = classify_relevance(_event("SOLS Seminar Series", categories=["Science"]))
        self.assertEqual(result["decision"], "review")

    def test_professional_and_career_development_category_alone_is_review_not_include(self):
        # Live testing found ASU applies this category broadly (e.g. to a
        # chocolate-science talk with zero other career/technical evidence)
        # - a bare category tag must not be enough on its own.
        result = classify_relevance(_event("The Curious Matter of Chocolate", categories=["Professional and career development"]))
        self.assertEqual(result["decision"], "review")

    def test_generic_academic_event_not_automatically_included(self):
        result = classify_relevance(_event("Seeking Justice in Arizona Fall Lecture Series", categories=["Academic events"]))
        self.assertEqual(result["decision"], "review")

    def test_unknown_event_with_no_evidence_is_review(self):
        result = classify_relevance(_event("Maosho"))
        self.assertEqual(result["decision"], "review")

    def test_audience_alone_is_insufficient(self):
        result = classify_relevance(_event("Some Event", audiences=["Current ASU students", "International students"]))
        self.assertEqual(result["decision"], "review")

    def test_audience_absence_does_not_cause_exclusion(self):
        # a strong include signal with NO audience tags at all still includes
        result = classify_relevance(_event("Career Fair", audiences=None))
        self.assertEqual(result["decision"], "include")


class ExcludeTests(unittest.TestCase):
    def test_sports_category_excludes(self):
        result = classify_relevance(_event("Sun Devil Soccer vs. North Carolina", categories=["Sports"]))
        self.assertEqual(result["decision"], "exclude")

    def test_yoga_keyword_excludes(self):
        result = classify_relevance(_event("Evening Yoga"))
        self.assertEqual(result["decision"], "exclude")

    def test_wellness_category_excludes(self):
        result = classify_relevance(_event("Meditation Circle", categories=["Health and Wellness"]))
        self.assertEqual(result["decision"], "exclude")

    def test_arts_and_entertainment_generally_excludes(self):
        result = classify_relevance(_event("ADRIAN: Designing Hollywood, Dressing America", categories=["Arts and Entertainment"]))
        self.assertEqual(result["decision"], "exclude")


class ConflictTests(unittest.TestCase):
    def test_conflicting_evidence_is_deterministic_review(self):
        event = _event(
            "Tech in Fashion: AI-Driven Costume Design", categories=["Arts and Entertainment"],
            description="A talk on artificial intelligence in costume design.",
        )
        first = classify_relevance(event)
        second = classify_relevance(event)
        self.assertEqual(first, second)
        self.assertEqual(first["decision"], "review")

    def test_arts_category_with_career_keyword_is_review_not_silent_include(self):
        result = classify_relevance(_event(
            "Networking Mixer at the Art Museum", categories=["Arts and Entertainment"],
        ))
        self.assertEqual(result["decision"], "review")

    def test_pure_arts_no_conflict_excludes(self):
        result = classify_relevance(_event("Broadway Show Night", categories=["Arts and Entertainment"]))
        self.assertEqual(result["decision"], "exclude")


class SignalReportingTests(unittest.TestCase):
    def test_signals_list_is_non_empty_when_evidence_exists(self):
        result = classify_relevance(_event("Career Fair", categories=["Professional and career development"]))
        self.assertTrue(result["signals"])

    def test_ai_word_boundary_not_substring(self):
        # "ai" must not fire on unrelated words like "said"/"maintain"
        result = classify_relevance(_event("Maintaining Campus Gardens"))
        self.assertEqual(result["decision"], "review")


if __name__ == "__main__":
    unittest.main()
