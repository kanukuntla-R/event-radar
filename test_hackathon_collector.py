"""Unit tests for hackathon_collector.collect(). All HTTP is mocked - no
real network access. Stdlib unittest + unittest.mock only.

Run with: python -m unittest test_hackathon_collector -v
"""
import copy
import socket
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import hackathon_collector as collector


class FakeResponse:
    """Minimal stand-in for the object urlopen() returns as a context manager."""

    def __init__(self, body: bytes, url, status=200, content_type="text/html; charset=utf-8"):
        self._body = body
        self._url = url
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def geturl(self):
        return self._url

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _event(**overrides):
    base = dict(
        name="Test Hack", source_url="https://example.com/hack",
        organizer="Test University", date_status="tba",
        start_date=None, end_date=None,
        registration_status="unknown", registration_url=None,
        application_deadline=None,
    )
    base.update(overrides)
    return base


PLAIN_HTML = """
<html><head><title>Test Hack 2026</title></head>
<body><p>Welcome to Test Hack, a weekend of building things with friends.</p></body></html>
"""

OPEN_HTML = """
<html><head><title>Test Hack 2026</title></head>
<body>
<p>Applications are open! <a href="https://example.com/apply">Apply Now</a></p>
<p>Join us October 3-4, 2026 for the event of the year.</p>
</body></html>
"""

TODAY = datetime(2026, 9, 4, tzinfo=timezone.utc)


class HttpOutcomeTests(unittest.TestCase):
    @patch("hackathon_collector.urlopen")
    def test_normal_200(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(PLAIN_HTML.encode(), "https://example.com/hack")
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "ok")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["page_title"], "Test Hack 2026")
        self.assertEqual(result["final_url"], "https://example.com/hack")

    @patch("hackathon_collector.urlopen")
    def test_redirect_followed(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(
            PLAIN_HTML.encode(), "https://example.com/hack-2026"  # differs from requested
        )
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "redirected")
        self.assertEqual(result["final_url"], "https://example.com/hack-2026")
        self.assertEqual(result["requested_url"], "https://example.com/hack")

    @patch("hackathon_collector.urlopen")
    def test_404_not_found(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError("https://example.com/hack", 404, "Not Found", {}, None)
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "not_found")
        self.assertEqual(result["http_status"], 404)

    @patch("hackathon_collector.urlopen")
    def test_500_server_error_is_unavailable(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError("https://example.com/hack", 500, "Server Error", {}, None)
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "unavailable")
        self.assertEqual(result["http_status"], 500)

    @patch("hackathon_collector.urlopen")
    def test_timeout_is_unavailable(self, mock_urlopen):
        mock_urlopen.side_effect = URLError(socket.timeout("timed out"))
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "unavailable")
        self.assertIsNone(result["http_status"])

    @patch("hackathon_collector.urlopen")
    def test_connection_error_is_unavailable(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("Name or service not known")
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "unavailable")

    def test_missing_source_url_is_unavailable(self):
        result = collector.collect(_event(source_url=None), now=TODAY)
        self.assertEqual(result["source_result"], "unavailable")


class ParsingTests(unittest.TestCase):
    @patch("hackathon_collector.urlopen")
    def test_registration_open_language_detected(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(OPEN_HTML.encode(), "https://example.com/hack")
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "ok")
        self.assertEqual(result["detected"]["registration_status"], "open")
        self.assertEqual(result["detected"]["registration_url"], "https://example.com/apply")
        self.assertEqual(result["detected"]["start_date"], "2026-10-03")
        self.assertEqual(result["detected"]["end_date"], "2026-10-04")
        self.assertEqual(result["detected"]["date_status"], "confirmed")

    @patch("hackathon_collector.urlopen")
    def test_page_with_no_usable_facts_stays_none(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(PLAIN_HTML.encode(), "https://example.com/hack")
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "ok")  # fetched+parsed fine, just nothing to find
        self.assertEqual(
            result["detected"],
            {
                "start_date": None, "end_date": None, "date_status": None,
                "registration_status": None, "registration_url": None,
                "application_deadline": None,
            },
        )

    @patch("hackathon_collector.urlopen")
    def test_empty_body_is_parse_failed(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(b"", "https://example.com/hack")
        result = collector.collect(_event(), now=TODAY)
        self.assertEqual(result["source_result"], "parse_failed")

    @patch("hackathon_collector.urlopen")
    def test_does_not_mutate_input_event(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(OPEN_HTML.encode(), "https://example.com/hack")
        event = _event()
        before = copy.deepcopy(event)
        collector.collect(event, now=TODAY)
        self.assertEqual(event, before)


if __name__ == "__main__":
    unittest.main()
