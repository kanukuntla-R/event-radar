"""Tests for auth.py's pure helpers (no Flask app/request context needed).
Full login/logout/CSRF/authorization flows are covered in test_app.py via
the Flask test client - see AuthTests/CsrfTests/AdminAuthorizationTests/
DashboardIsolationTests there. Stdlib unittest only.

Run with: python -m unittest test_auth -v
"""
import unittest

from auth import is_safe_next_url


class SafeNextUrlTests(unittest.TestCase):
    def test_relative_local_path_is_safe(self):
        self.assertTrue(is_safe_next_url("/review-queue"))

    def test_root_path_is_safe(self):
        self.assertTrue(is_safe_next_url("/"))

    def test_absolute_external_url_is_unsafe(self):
        self.assertFalse(is_safe_next_url("https://evil.example.com/"))

    def test_protocol_relative_url_is_unsafe(self):
        self.assertFalse(is_safe_next_url("//evil.example.com/"))

    def test_empty_is_unsafe(self):
        self.assertFalse(is_safe_next_url(""))

    def test_none_is_unsafe(self):
        self.assertFalse(is_safe_next_url(None))

    def test_path_without_leading_slash_is_unsafe(self):
        self.assertFalse(is_safe_next_url("evil.example.com"))

    def test_javascript_scheme_is_unsafe(self):
        self.assertFalse(is_safe_next_url("javascript:alert(1)"))


if __name__ == "__main__":
    unittest.main()
