"""Tests for source_recovery.py (normalize_url/classify_candidate/
rank_candidate). Pure logic, no SQLite, no HTTP. Stdlib unittest only.

Run with: python -m unittest test_source_recovery -v
"""
import unittest

from source_recovery import normalize_url, classify_candidate, rank_candidate


def _event(**overrides):
    base = dict(
        name="MHacks", organizer="Major League Hacking", source_url="https://mhacks.org/",
    )
    base.update(overrides)
    return base


def _candidate(url, title=None, evidence=None):
    return {"candidate_url": url, "title": title, "evidence": evidence}


class NormalizeUrlTests(unittest.TestCase):
    def test_none_and_empty_return_none(self):
        self.assertIsNone(normalize_url(None))
        self.assertIsNone(normalize_url(""))

    def test_lowercases_scheme_and_host(self):
        self.assertEqual(normalize_url("HTTPS://Example.COM/Path"), "https://example.com/Path")

    def test_strips_trailing_slash_but_keeps_root(self):
        self.assertEqual(normalize_url("https://example.com/path/"), "https://example.com/path")
        self.assertEqual(normalize_url("https://example.com/"), "https://example.com/")

    def test_drops_fragment(self):
        self.assertEqual(normalize_url("https://example.com/page#section"), "https://example.com/page")

    def test_deterministic_for_equivalent_urls(self):
        a = normalize_url("HTTPS://Example.com/Page/")
        b = normalize_url("https://example.com/Page")
        self.assertEqual(a, b)


class ClassifyCandidateTests(unittest.TestCase):
    def test_same_registrable_domain_as_source_is_official_event(self):
        event = _event(source_url="https://mhacks.org/2026")
        candidate_type = classify_candidate(event, _candidate("https://www.mhacks.org/apply"))
        self.assertEqual(candidate_type, "official_event")

    def test_edu_domain_is_official_university(self):
        event = _event(source_url="https://old-site.example.com")
        candidate_type = classify_candidate(event, _candidate("https://umich.edu/mhacks"))
        self.assertEqual(candidate_type, "official_university")

    def test_mlh_domain_is_mlh(self):
        event = _event(source_url="https://old-site.example.com")
        candidate_type = classify_candidate(event, _candidate("https://mlh.io/events/mhacks"))
        self.assertEqual(candidate_type, "mlh")

    def test_social_domain_is_official_social(self):
        event = _event(source_url="https://old-site.example.com")
        candidate_type = classify_candidate(event, _candidate("https://instagram.com/mhacks"))
        self.assertEqual(candidate_type, "official_social")

    def test_organizer_token_in_domain_is_official_organizer(self):
        event = _event(source_url="https://old-site.example.com", organizer="Hackathon Org")
        candidate_type = classify_candidate(event, _candidate("https://hackathonorg.com/events"))
        self.assertEqual(candidate_type, "official_organizer")

    def test_unrelated_domain_is_other(self):
        event = _event(source_url="https://old-site.example.com", organizer="Major League Hacking")
        candidate_type = classify_candidate(event, _candidate("https://some-random-aggregator.net/list"))
        self.assertEqual(candidate_type, "other")


class RankCandidateTests(unittest.TestCase):
    def test_official_event_outranks_aggregator(self):
        event = _event(source_url="https://mhacks.org/")
        official = rank_candidate(event, _candidate("https://www.mhacks.org/apply"))
        aggregator = rank_candidate(event, _candidate("https://some-random-aggregator.net/list"))
        self.assertEqual(official["confidence"], "high")
        self.assertEqual(aggregator["confidence"], "low")

    def test_official_university_is_high_confidence(self):
        event = _event(source_url="https://old-site.example.com")
        result = rank_candidate(event, _candidate("https://umich.edu/mhacks"))
        self.assertEqual(result["candidate_type"], "official_university")
        self.assertEqual(result["confidence"], "high")

    def test_mlh_is_medium_confidence_by_default(self):
        event = _event(source_url="https://old-site.example.com")
        result = rank_candidate(event, _candidate("https://mlh.io/events/some-other-hack", title="Some Other Hack"))
        self.assertEqual(result["candidate_type"], "mlh")
        self.assertEqual(result["confidence"], "medium")

    def test_mlh_with_brand_match_bumps_to_high(self):
        event = _event(name="MHacks", source_url="https://old-site.example.com")
        result = rank_candidate(event, _candidate("https://mlh.io/events/mhacks", title="MHacks 2026 - MLH"))
        self.assertEqual(result["confidence"], "high")

    def test_unknown_source_gets_low_confidence(self):
        event = _event(source_url="https://old-site.example.com")
        result = rank_candidate(event, _candidate("https://some-random-aggregator.net/list"))
        self.assertEqual(result["candidate_type"], "other")
        self.assertEqual(result["confidence"], "low")

    def test_result_shape_and_normalization(self):
        event = _event(source_url="https://mhacks.org/")
        result = rank_candidate(event, _candidate("HTTPS://WWW.MHACKS.ORG/Apply/", evidence="Redirect target"))
        self.assertEqual(result, {
            "candidate_url": "https://www.mhacks.org/Apply",
            "candidate_type": "official_event",
            "title": None,
            "evidence": "Redirect target",
            "confidence": "high",
        })

    def test_does_not_mutate_inputs(self):
        event = _event()
        candidate = _candidate("https://mlh.io/events/mhacks", title="MHacks")
        event_before, candidate_before = dict(event), dict(candidate)
        rank_candidate(event, candidate)
        self.assertEqual(event, event_before)
        self.assertEqual(candidate, candidate_before)


if __name__ == "__main__":
    unittest.main()
