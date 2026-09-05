"""Tests for hackathon_source_recovery_collector.discover_candidates().
Fully offline: `fetch` is always injected, no real network calls. Stdlib
unittest only.

Run with: python -m unittest test_hackathon_source_recovery_collector -v
"""
import unittest

from hackathon_source_recovery_collector import discover_candidates


def _event(**overrides):
    base = dict(source_url="https://old-mhacks.example.com/", organizer="Major League Hacking")
    base.update(overrides)
    return base


def _fetch(html, final_url):
    def fetch(url, timeout):
        return html, final_url
    return fetch


def _raising_fetch(exc):
    def fetch(url, timeout):
        raise exc
    return fetch


class NoSourceTests(unittest.TestCase):
    def test_no_source_url_returns_empty(self):
        result = discover_candidates(_event(source_url=None))
        self.assertEqual(result, [])

    def test_fetch_failure_returns_empty_not_raise(self):
        result = discover_candidates(_event(), fetch=_raising_fetch(OSError("unreachable")))
        self.assertEqual(result, [])


class RedirectTests(unittest.TestCase):
    def test_redirect_to_different_domain_is_a_candidate(self):
        fetch = _fetch("<html></html>", "https://new-mhacks.example.org/2026")
        result = discover_candidates(_event(source_url="https://old-mhacks.example.com/"), fetch=fetch)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["candidate_url"], "https://new-mhacks.example.org/2026")

    def test_redirect_to_same_domain_is_not_a_candidate(self):
        fetch = _fetch("<html></html>", "https://old-mhacks.example.com/new-path")
        result = discover_candidates(_event(source_url="https://old-mhacks.example.com/"), fetch=fetch)
        self.assertEqual(result, [])


class LinkDiscoveryTests(unittest.TestCase):
    def test_edu_link_is_a_candidate(self):
        html = '<a href="https://umich.edu/mhacks">University page</a>'
        result = discover_candidates(_event(), fetch=_fetch(html, "https://old-mhacks.example.com/"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["candidate_url"], "https://umich.edu/mhacks")
        self.assertEqual(result[0]["title"], "University page")

    def test_mlh_link_is_a_candidate(self):
        html = '<a href="https://mlh.io/events/mhacks">MLH listing</a>'
        result = discover_candidates(_event(), fetch=_fetch(html, "https://old-mhacks.example.com/"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["candidate_url"], "https://mlh.io/events/mhacks")

    def test_social_link_is_a_candidate(self):
        html = '<a href="https://instagram.com/mhacks">Instagram</a>'
        result = discover_candidates(_event(), fetch=_fetch(html, "https://old-mhacks.example.com/"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["candidate_url"], "https://instagram.com/mhacks")

    def test_organizer_token_link_is_a_candidate(self):
        html = '<a href="https://mhackathons.org/about">About MHacks org</a>'
        result = discover_candidates(
            _event(organizer="MHackathons"), fetch=_fetch(html, "https://old-mhacks.example.com/")
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["candidate_url"], "https://mhackathons.org/about")

    def test_unrelated_random_domain_is_ignored(self):
        html = '<a href="https://some-random-aggregator.net/list">See all hackathons</a>'
        result = discover_candidates(_event(), fetch=_fetch(html, "https://old-mhacks.example.com/"))
        self.assertEqual(result, [])

    def test_same_domain_link_is_ignored(self):
        html = '<a href="https://old-mhacks.example.com/schedule">Schedule</a>'
        result = discover_candidates(_event(), fetch=_fetch(html, "https://old-mhacks.example.com/"))
        self.assertEqual(result, [])

    def test_duplicate_urls_are_not_repeated(self):
        html = (
            '<a href="https://mlh.io/events/mhacks">MLH</a>'
            '<a href="https://mlh.io/events/mhacks">MLH again</a>'
        )
        result = discover_candidates(_event(), fetch=_fetch(html, "https://old-mhacks.example.com/"))
        self.assertEqual(len(result), 1)

    def test_does_not_mutate_event(self):
        event = _event()
        before = dict(event)
        discover_candidates(event, fetch=_fetch("<html></html>", "https://old-mhacks.example.com/"))
        self.assertEqual(event, before)


if __name__ == "__main__":
    unittest.main()
