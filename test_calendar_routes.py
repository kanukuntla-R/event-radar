"""Tests for V0.10D private per-user calendar token routes: GET/POST
/calendar (settings), POST /calendar/token/{create,rotate,revoke}, and
GET /calendar/<token>.ics. Reuses test_app.py's AppTestCase (same
throwaway-DB-before-import setup, same auto-login-as-owner-admin
default) rather than duplicating that plumbing. Stdlib unittest only.

Run with: python -m unittest test_calendar_routes -v
"""
import re
import unittest

from test_app import AppTestCase, _insert_event, OWNER_TEST_PASSWORD, _create_friend
from user_store import get_active_calendar_token_for_user, get_user_for_calendar_token


def _extract_token_from_url(url):
    # .../calendar/<token>.ics
    match = re.search(r"/calendar/([^/]+)\.ics", url)
    return match.group(1) if match else None


class CalendarSettingsPageTests(AppTestCase):
    def test_no_active_token_shows_create_option(self):
        html = self.client.get("/calendar").data.decode()
        self.assertIn("No active calendar subscription", html)
        self.assertIn("/calendar/token/create", html)

    def test_active_token_shows_status_not_raw_url(self):
        self._post("/calendar/token/create")
        html = self.client.get("/calendar").data.decode()
        self.assertIn("Calendar subscription active", html)
        self.assertIn("cannot be displayed again", html)

    def test_unauthenticated_get_redirects_to_login(self):
        self._logout()
        resp = self.client.get("/calendar", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])


class CreateCalendarTokenRouteTests(AppTestCase):
    def test_create_shows_url_once(self):
        resp = self._post("/calendar/token/create")
        html = resp.data.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/calendar/", html)
        self.assertIn(".ics", html)

    def test_created_token_actually_resolves(self):
        html = self._post("/calendar/token/create").data.decode()
        match = re.search(r"http\S*/calendar/([^/\s<]+)\.ics", html) or re.search(r"/calendar/([^/\s<]+)\.ics", html)
        token = match.group(1)
        user = get_user_for_calendar_token(self.conn, token)
        self.assertEqual(user["id"], self.owner["id"])

    def test_second_create_without_revoke_does_not_create_second_token(self):
        self._post("/calendar/token/create")
        self._post("/calendar/token/create")
        count = self.conn.execute(
            "SELECT COUNT(*) FROM calendar_tokens WHERE user_id = ? AND revoked_at IS NULL",
            (self.owner["id"],),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_unauthenticated_create_blocked_no_mutation(self):
        self._logout()
        resp = self._post("/calendar/token/create")
        self.assertIn(resp.status_code, (302, 400))
        count = self.conn.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        self.assertEqual(count, 0)

    def test_missing_csrf_blocked_no_mutation(self):
        resp = self.client.post("/calendar/token/create", data={})
        self.assertEqual(resp.status_code, 400)
        count = self.conn.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        self.assertEqual(count, 0)

    def test_wrong_csrf_blocked_no_mutation(self):
        resp = self.client.post("/calendar/token/create", data={"csrf_token": "wrong"})
        self.assertEqual(resp.status_code, 400)
        count = self.conn.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        self.assertEqual(count, 0)

    def test_never_takes_user_id_from_form_field(self):
        friend = _create_friend(self.conn)
        # Attempt to smuggle a different user_id in the form body.
        self._post("/calendar/token/create", {"user_id": str(friend["id"])})
        owner_has_token = get_active_calendar_token_for_user(self.conn, self.owner["id"]) is not None
        friend_has_token = get_active_calendar_token_for_user(self.conn, friend["id"]) is not None
        self.assertTrue(owner_has_token)
        self.assertFalse(friend_has_token)

    def test_response_has_no_store_and_no_referrer_headers(self):
        resp = self._post("/calendar/token/create")
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")

    def test_second_create_error_is_generic_not_exposing_user_id(self):
        self._post("/calendar/token/create")
        resp = self._post("/calendar/token/create")
        html = resp.data.decode()
        error = re.search(r'proposal-error">([^<]*)<', html).group(1)
        self.assertIn("already active", error)
        self.assertNotIn(f"User {self.owner['id']}", error)
        self.assertNotIn("already has an active calendar token", error)

    def test_ordinary_get_calendar_does_not_expose_raw_token(self):
        create_html = self._post("/calendar/token/create").data.decode()
        token = _extract_token_from_url(create_html)
        get_html = self.client.get("/calendar").data.decode()
        self.assertNotIn(token, get_html)


class RotateCalendarTokenRouteTests(AppTestCase):
    def test_rotate_shows_new_url_and_old_stops_working(self):
        first_html = self._post("/calendar/token/create").data.decode()
        old_token = _extract_token_from_url(first_html)

        rotated_html = self._post("/calendar/token/rotate").data.decode()
        new_token = _extract_token_from_url(rotated_html)

        self.assertIsNotNone(new_token)
        self.assertNotEqual(old_token, new_token)
        self.assertIsNone(get_user_for_calendar_token(self.conn, old_token))
        self.assertIsNotNone(get_user_for_calendar_token(self.conn, new_token))

    def test_rotate_without_existing_token_still_works(self):
        html = self._post("/calendar/token/rotate").data.decode()
        self.assertIn(".ics", html)

    def test_rotate_only_affects_own_token(self):
        friend = _create_friend(self.conn)
        from user_store import create_calendar_token
        friend_token = create_calendar_token(self.conn, friend["id"])

        self._post("/calendar/token/create")
        self._post("/calendar/token/rotate")

        self.assertIsNotNone(get_user_for_calendar_token(self.conn, friend_token))

    def test_missing_csrf_blocked_no_mutation(self):
        self._post("/calendar/token/create")
        before = get_active_calendar_token_for_user(self.conn, self.owner["id"])
        resp = self.client.post("/calendar/token/rotate", data={})
        self.assertEqual(resp.status_code, 400)
        after = get_active_calendar_token_for_user(self.conn, self.owner["id"])
        self.assertEqual(before["token_hash"], after["token_hash"])

    def test_unauthenticated_rotate_blocked(self):
        self._post("/calendar/token/create")
        self._logout()
        resp = self._post("/calendar/token/rotate")
        self.assertIn(resp.status_code, (302, 400))

    def test_response_has_no_store_and_no_referrer_headers(self):
        self._post("/calendar/token/create")
        resp = self._post("/calendar/token/rotate")
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")


class RevokeCalendarTokenRouteTests(AppTestCase):
    def test_revoke_makes_old_url_404(self):
        html = self._post("/calendar/token/create").data.decode()
        token = _extract_token_from_url(html)
        self._post("/calendar/token/revoke")
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertEqual(resp.status_code, 404)

    def test_revoke_does_not_create_replacement(self):
        self._post("/calendar/token/create")
        self._post("/calendar/token/revoke")
        self.assertIsNone(get_active_calendar_token_for_user(self.conn, self.owner["id"]))

    def test_can_create_again_after_revoke(self):
        self._post("/calendar/token/create")
        self._post("/calendar/token/revoke")
        resp = self._post("/calendar/token/create")
        self.assertEqual(resp.status_code, 200)

    def test_revoke_only_affects_own_token(self):
        friend = _create_friend(self.conn)
        from user_store import create_calendar_token
        friend_token = create_calendar_token(self.conn, friend["id"])
        self._post("/calendar/token/create")

        self._post("/calendar/token/revoke")

        self.assertIsNotNone(get_user_for_calendar_token(self.conn, friend_token))

    def test_missing_csrf_blocked_no_mutation(self):
        self._post("/calendar/token/create")
        resp = self.client.post("/calendar/token/revoke", data={})
        self.assertEqual(resp.status_code, 400)
        self.assertIsNotNone(get_active_calendar_token_for_user(self.conn, self.owner["id"]))

    def test_unauthenticated_revoke_blocked(self):
        self._post("/calendar/token/create")
        self._logout()
        self._post("/calendar/token/revoke")
        self.assertIsNotNone(get_active_calendar_token_for_user(self.conn, self.owner["id"]))


class PrivateIcsRouteTests(AppTestCase):
    def test_valid_token_returns_calendar(self):
        event_id = _insert_event(self.conn, name="Career Fair", event_state="active")
        self._post(f"/event/{event_id}/interested")
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("SUMMARY:Career Fair", resp.data.decode())

    def test_random_token_returns_404(self):
        resp = self.client.get("/calendar/totally-made-up-token.ics")
        self.assertEqual(resp.status_code, 404)

    def test_revoked_token_returns_404(self):
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        self._post("/calendar/token/revoke")
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertEqual(resp.status_code, 404)

    def test_response_has_private_no_store_and_no_referrer_headers(self):
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))
        self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")

    def test_does_not_require_csrf(self):
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        # A plain GET, no csrf_token anywhere - must still succeed.
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertEqual(resp.status_code, 200)

    def test_does_not_require_login(self):
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        self._logout()
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertEqual(resp.status_code, 200)

    def test_missing_status_row_excluded(self):
        _insert_event(self.conn, name="Untouched Event", event_state="active")
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertNotIn("Untouched Event", resp.data.decode())

    def test_watching_excluded(self):
        event_id = _insert_event(self.conn, name="Watch Only", event_state="active")
        self._post(f"/event/{event_id}/watching")
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertNotIn("Watch Only", resp.data.decode())

    def test_ignored_excluded(self):
        event_id = _insert_event(self.conn, name="Ignored Event", event_state="active")
        self._post(f"/event/{event_id}/ignore")
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertNotIn("Ignored Event", resp.data.decode())

    def test_inactive_interested_excluded(self):
        event_id = _insert_event(self.conn, name="Inactive Interest", event_state="inactive")
        self._post(f"/event/{event_id}/interested")
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertNotIn("Inactive Interest", resp.data.decode())

    def test_legacy_events_status_irrelevant(self):
        event_id = _insert_event(self.conn, name="Legacy Conflict", event_state="active")
        self.conn.execute("UPDATE events SET status = 'ignored' WHERE id = ?", (event_id,))
        self.conn.commit()
        self._post(f"/event/{event_id}/interested")
        token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())
        resp = self.client.get(f"/calendar/{token}.ics")
        self.assertIn("Legacy Conflict", resp.data.decode())


class SessionVsTokenOwnershipTests(AppTestCase):
    """V0.10D section 7: the private feed belongs to the TOKEN owner,
    never the browser session - regardless of who (if anyone) is
    logged in when the token URL is fetched."""

    def setUp(self):
        super().setUp()
        self.friend = _create_friend(self.conn)

    def test_logged_in_as_friend_but_requesting_owner_token_gives_owner_feed(self):
        event_id = _insert_event(self.conn, name="Owner Only Event", event_state="active")
        self._post(f"/event/{event_id}/interested")  # owner interested
        owner_token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        self._logout()
        self._login(self.friend["email"], "friend-test-password")

        resp = self.client.get(f"/calendar/{owner_token}.ics")
        self.assertIn("Owner Only Event", resp.data.decode())

    def test_unauthenticated_request_with_valid_token_gives_correct_feed(self):
        event_id = _insert_event(self.conn, name="Anon Fetch Event", event_state="active")
        self._post(f"/event/{event_id}/interested")
        owner_token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        self._logout()

        resp = self.client.get(f"/calendar/{owner_token}.ics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Anon Fetch Event", resp.data.decode())


class CrossUserCalendarTests(AppTestCase):
    """V0.10D section 19: owner + Alice + Bob, independent feeds."""

    def setUp(self):
        super().setUp()
        self.alice = _create_friend(self.conn, "alice@example.com", "alice-test-password")
        self.bob = _create_friend(self.conn, "bob@example.com", "bob-test-password")

    def _login_as(self, email, password):
        self._logout()
        self._login(email, password)

    def test_each_token_feed_contains_exactly_the_right_events(self):
        event_x = _insert_event(self.conn, name="Event X", event_state="active")
        event_y = _insert_event(self.conn, name="Event Y", event_state="active")

        self._login_as("alice@example.com", "alice-test-password")
        self._post(f"/event/{event_x}/interested")
        alice_token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        self._login_as("bob@example.com", "bob-test-password")
        self._post(f"/event/{event_x}/watching")
        self._post(f"/event/{event_y}/interested")
        bob_token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        self._login_as(self.owner["email"], OWNER_TEST_PASSWORD)
        self._post(f"/event/{event_y}/ignore")
        owner_token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        alice_ics = self.client.get(f"/calendar/{alice_token}.ics").data.decode()
        bob_ics = self.client.get(f"/calendar/{bob_token}.ics").data.decode()
        owner_ics = self.client.get(f"/calendar/{owner_token}.ics").data.decode()

        self.assertIn("Event X", alice_ics)
        self.assertNotIn("Event Y", alice_ics)

        self.assertNotIn("Event X", bob_ics)  # Bob is only Watching X
        self.assertIn("Event Y", bob_ics)

        self.assertNotIn("Event X", owner_ics)
        self.assertNotIn("Event Y", owner_ics)  # owner ignored Y

    def test_alice_rotating_does_not_affect_bob(self):
        self._login_as("alice@example.com", "alice-test-password")
        self._post("/calendar/token/create")
        self._login_as("bob@example.com", "bob-test-password")
        bob_token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        self._login_as("alice@example.com", "alice-test-password")
        self._post("/calendar/token/rotate")

        self.assertIsNotNone(get_user_for_calendar_token(self.conn, bob_token))

    def test_alice_revoking_does_not_affect_bob(self):
        self._login_as("alice@example.com", "alice-test-password")
        self._post("/calendar/token/create")
        self._login_as("bob@example.com", "bob-test-password")
        bob_token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        self._login_as("alice@example.com", "alice-test-password")
        self._post("/calendar/token/revoke")

        self.assertIsNotNone(get_user_for_calendar_token(self.conn, bob_token))

    def test_bob_cannot_manage_alice_token_via_form_tampering(self):
        self._login_as("alice@example.com", "alice-test-password")
        alice_token = _extract_token_from_url(self._post("/calendar/token/create").data.decode())

        self._login_as("bob@example.com", "bob-test-password")
        # Bob tries to rotate/revoke while authenticated as himself - the
        # route must only ever act on g.user (Bob), never on a
        # request-supplied identifier, so Alice's token must survive.
        self._post("/calendar/token/rotate")
        self._post("/calendar/token/revoke")

        self.assertIsNotNone(get_user_for_calendar_token(self.conn, alice_token))


if __name__ == "__main__":
    unittest.main()
