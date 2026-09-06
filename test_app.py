"""Flask dashboard tests for the proposal review UI (V0.4C). Runs entirely
against temporary SQLite files (db.DB_PATH monkeypatched) - never touches the
real data/events.db. Stdlib unittest only.

Run with: python -m unittest test_app -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import db as db_module

# app.py calls init_db() at import time, so db.DB_PATH must already point at
# a throwaway file *before* app is first imported anywhere in this process -
# otherwise that first import would create/touch the real data/events.db.
_import_tmpdir = tempfile.TemporaryDirectory()
db_module.DB_PATH = Path(_import_tmpdir.name) / "import-events.db"

# app.py now raises immediately at import time if EVENT_RADAR_SECRET_KEY is
# unset (V0.10C - fail at startup, not on the first request that touches
# the session) - so this must be set *before* importing app, exactly like
# a real deployment's EnvironmentFile, just with a throwaway test value.
os.environ["EVENT_RADAR_SECRET_KEY"] = "test-only-secret-key-do-not-use-in-production"

import app as app_module  # noqa: E402
from user_store import (  # noqa: E402
    get_or_create_bootstrap_owner, get_user_event_status, set_user_event_status,
    create_user, set_user_password,
)

OWNER_TEST_PASSWORD = "owner-test-password-not-real"  # noqa: S105 (test fixture, not a real credential)


def _insert_event(conn, **overrides):
    """Inserts a shared event. A non-default `status` kwarg is mirrored
    into user_event_status for the bootstrap owner too (in addition to
    the legacy events.status column) - this is test convenience only, so
    existing tests seeding "this event is Interested/Watching" keep
    working against the new per-user read path without every call site
    needing to change. Production discovery code does NOT do this (see
    V0.10B: a newly discovered event intentionally gets no
    user_event_status row until a user acts on it)."""
    fields = dict(
        name="Test Hack", date_status="confirmed", start_date="2026-10-03",
        end_date="2026-10-04", registration_status="unknown", registration_url=None,
        application_deadline=None, status="new",
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    event_id = cur.lastrowid
    if fields["status"] != "new":
        owner = get_or_create_bootstrap_owner(conn)
        set_user_event_status(conn, owner["id"], event_id, fields["status"])
    return event_id


def _insert_proposal(conn, event_id, field, old_value, new_value, status="pending", **overrides):
    fields = dict(
        event_id=event_id, field=field, old_value=old_value, new_value=new_value,
        evidence="apply today", source_result="ok", detected_at="2026-09-05T00:00:00+00:00",
        status=status, resolved_at=None,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(
        f"INSERT INTO event_change_proposals ({columns}) VALUES ({placeholders})", fields
    )
    conn.commit()
    return cur.lastrowid


def _insert_recovery_candidate(conn, event_id, candidate_url="https://mlh.io/events/test", status="pending", **overrides):
    fields = dict(
        event_id=event_id, candidate_url=candidate_url, candidate_type="mlh",
        title="Test Hack - MLH", evidence="Linked from source page", confidence="medium",
        discovered_at="2026-09-05T00:00:00+00:00", status=status, resolved_at=None,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(
        f"INSERT INTO source_recovery_candidates ({columns}) VALUES ({placeholders})", fields
    )
    conn.commit()
    return cur.lastrowid


def _insert_review_candidate(conn, status="pending", **overrides):
    fields = dict(
        name="Maosho", event_type="asu", event_subtype="other",
        description="An academic talk about a curious subject.",
        start_date="2026-09-15", end_date="2026-09-15", start_time="17:00", end_time="18:00",
        timezone="America/Phoenix", location="MU Alumni Lounge", location_type="in_person",
        location_area="tempe", source="ASU Events", source_url="https://asuevents.asu.edu/event/maosho",
        organizer="ASU Events", registration_status="unknown", registration_url=None,
        application_deadline=None, review_reason="no clear include or exclude evidence",
        review_signals=json.dumps(["supporting_category:academic events"]),
        discovered_at="2026-09-05T00:00:00+00:00", status=status, resolved_at=None,
    )
    fields.update(overrides)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    cur = conn.execute(f"INSERT INTO event_review_candidates ({columns}) VALUES ({placeholders})", fields)
    conn.commit()
    return cur.lastrowid


class AppTestCase(unittest.TestCase):
    """Base test case. Auto-logs-in as the bootstrap owner (now an
    ordinary admin account) in setUp, matching this suite's pre-V0.10C
    single-user assumption for the bulk of existing coverage. Tests that
    are specifically about the auth/authorization/CSRF boundary itself
    (see AuthTests, CsrfTests, AdminAuthorizationTests,
    DashboardIsolationTests below) manage login/logout explicitly."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self._tmpdir.name) / "events.db"
        app_module.init_db()
        self.client = app_module.app.test_client()
        self.conn = db_module.get_db()
        self.owner = get_or_create_bootstrap_owner(self.conn)
        set_user_password(self.conn, self.owner["id"], OWNER_TEST_PASSWORD)
        self.conn.commit()
        self._login(self.owner["email"], OWNER_TEST_PASSWORD)

    def tearDown(self):
        self.conn.close()
        self._tmpdir.cleanup()

    def _csrf_token(self):
        with self.client.session_transaction() as sess:
            sess.setdefault("csrf_token", "test-csrf-token-0123456789")
            return sess["csrf_token"]

    def _post(self, url, data=None):
        payload = dict(data or {})
        payload["csrf_token"] = self._csrf_token()
        return self.client.post(url, data=payload)

    def _login(self, email, password):
        return self._post("/login", {"email": email, "password": password})

    def _logout(self):
        return self._post("/logout")

    def _proposal(self, proposal_id):
        return self.conn.execute(
            "SELECT * FROM event_change_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()

    def _event(self, event_id):
        return self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    def _owner_id(self):
        return get_or_create_bootstrap_owner(self.conn)["id"]

    def _owner_status(self, event_id):
        return get_user_event_status(self.conn, self._owner_id(), event_id)

    def _set_owner_status(self, event_id, status):
        set_user_event_status(self.conn, self._owner_id(), event_id, status)

    def _recovery_candidate(self, candidate_id):
        return self.conn.execute(
            "SELECT * FROM source_recovery_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()

    def _review_candidate(self, review_id):
        return self.conn.execute(
            "SELECT * FROM event_review_candidates WHERE id = ?", (review_id,)
        ).fetchone()


class DashboardRenderingTests(AppTestCase):
    def test_pending_proposal_displayed_under_correct_event(self):
        event_id = _insert_event(self.conn, name="Cal Hacks")
        _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        html = self.client.get("/").data.decode()
        self.assertIn("Cal Hacks", html)
        self.assertIn("Detected changes", html)
        self.assertIn("Registration status", html)
        self.assertIn("Unknown", html)
        self.assertIn("Open", html)

    def test_accepted_proposal_not_displayed(self):
        event_id = _insert_event(self.conn)
        _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open", status="accepted")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Detected changes", html)

    def test_dismissed_proposal_not_displayed(self):
        event_id = _insert_event(self.conn)
        _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open", status="dismissed")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Detected changes", html)

    def test_only_pending_proposals_rendered(self):
        event_id = _insert_event(self.conn)
        _insert_proposal(self.conn, event_id, "registration_status", "unknown", "not_open", status="accepted")
        _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open", status="pending")
        html = self.client.get("/").data.decode()
        self.assertIn("Detected changes", html)
        self.assertIn("Open", html)
        self.assertNotIn("Not open", html)

    def test_evidence_shown_when_available(self):
        event_id = _insert_event(self.conn)
        _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open", evidence="apply today")
        html = self.client.get("/").data.decode()
        self.assertIn("apply today", html)

    def test_null_old_value_shown_as_not_set(self):
        event_id = _insert_event(self.conn, registration_url=None)
        _insert_proposal(self.conn, event_id, "registration_url", None, "https://example.com/apply")
        html = self.client.get("/").data.decode()
        self.assertIn("Not set", html)


class AcceptRouteTests(AppTestCase):
    def test_accept_post_updates_event(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        resp = self._post(f"/proposal/{pid}/accept")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id)["registration_status"], "open")
        self.assertEqual(self._proposal(pid)["status"], "accepted")

    def test_accepted_proposal_disappears_from_pending_ui(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        self._post(f"/proposal/{pid}/accept")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Detected changes", html)

    def test_stale_accept_handled_gracefully(self):
        event_id = _insert_event(self.conn, registration_status="closed")
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        resp = self._post(f"/proposal/{pid}/accept")
        self.assertEqual(resp.status_code, 200)  # rendered dashboard, not a 500
        self.assertEqual(self._event(event_id)["registration_status"], "closed")
        self.assertEqual(self._proposal(pid)["status"], "pending")

    def test_nonexistent_proposal_does_not_crash(self):
        resp = self._post("/proposal/99999/accept")
        self.assertEqual(resp.status_code, 200)

    def test_already_resolved_proposal_accept_does_not_crash(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open", status="accepted")
        resp = self._post(f"/proposal/{pid}/accept")
        self.assertEqual(resp.status_code, 200)

    def test_accept_via_get_is_not_allowed(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        resp = self.client.get(f"/proposal/{pid}/accept")
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(self._proposal(pid)["status"], "pending")


class DismissRouteTests(AppTestCase):
    def test_dismiss_post_leaves_event_unchanged(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        before = self._event(event_id)
        resp = self._post(f"/proposal/{pid}/dismiss")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id), before)
        self.assertEqual(self._proposal(pid)["status"], "dismissed")

    def test_dismissed_proposal_disappears_from_pending_ui(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        self._post(f"/proposal/{pid}/dismiss")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Detected changes", html)

    def test_nonexistent_proposal_dismiss_does_not_crash(self):
        resp = self._post("/proposal/99999/dismiss")
        self.assertEqual(resp.status_code, 200)

    def test_dismiss_via_get_is_not_allowed(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        resp = self.client.get(f"/proposal/{pid}/dismiss")
        self.assertEqual(resp.status_code, 405)


class ExistingBehaviorRegressionTests(AppTestCase):
    def test_event_status_controls_still_work(self):
        event_id = _insert_event(self.conn)
        resp = self._post(f"/event/{event_id}/interested")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._owner_status(event_id), "interested")
        self.assertEqual(self._event(event_id)["status"], "new")  # legacy untouched

    def test_dashboard_renders_with_no_proposals(self):
        _insert_event(self.conn)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Detected changes", resp.data)


class SourceHealthDashboardTests(AppTestCase):
    def test_healthy_active_event_shows_no_warning(self):
        _insert_event(self.conn, consecutive_failures=0, last_check_status="ok")
        html = self.client.get("/").data.decode()
        self.assertNotIn("source-health-warning", html)
        self.assertNotIn("source-health-review", html)

    def test_failed_active_event_shows_source_issue(self):
        _insert_event(self.conn, consecutive_failures=1, last_check_status="unavailable")
        html = self.client.get("/").data.decode()
        self.assertIn("source-health-warning", html)
        self.assertIn("Recent source issue", html)

    def test_three_or_more_failures_shows_review_recommended(self):
        _insert_event(self.conn, consecutive_failures=3, last_check_status="unavailable")
        html = self.client.get("/").data.decode()
        self.assertIn("source-health-review", html)
        self.assertIn("Source review recommended", html)

    def test_inactive_event_does_not_show_active_source_warning(self):
        _insert_event(
            self.conn, event_state="inactive", inactive_reason="manual",
            consecutive_failures=5, last_check_status="unavailable",
        )
        html = self.client.get("/").data.decode()
        self.assertNotIn("source-health-review", html)
        self.assertNotIn("Source review recommended", html)
        self.assertIn("source-health-inactive", html)

    def test_inactive_event_renders_with_badge_and_reactivate(self):
        event_id = _insert_event(self.conn, event_state="inactive", inactive_reason="canceled")
        html = self.client.get("/").data.decode()
        self.assertIn("badge-inactive", html)
        self.assertIn(f'/event/{event_id}/reactivate', html)


class InactivateRouteTests(AppTestCase):
    def test_post_inactive_changes_event_state(self):
        event_id = _insert_event(self.conn, event_state="active")
        resp = self._post(f"/event/{event_id}/inactive")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id)["event_state"], "inactive")

    def test_post_inactive_sets_default_reason_manual(self):
        event_id = _insert_event(self.conn)
        self._post(f"/event/{event_id}/inactive")
        self.assertEqual(self._event(event_id)["inactive_reason"], "manual")

    def test_post_inactive_accepts_allowed_reason(self):
        event_id = _insert_event(self.conn)
        self._post(f"/event/{event_id}/inactive", data={"inactive_reason": "canceled"})
        self.assertEqual(self._event(event_id)["inactive_reason"], "canceled")

    def test_post_inactive_rejects_unknown_reason(self):
        event_id = _insert_event(self.conn)
        self._post(f"/event/{event_id}/inactive", data={"inactive_reason": "bogus"})
        self.assertEqual(self._event(event_id)["inactive_reason"], "manual")

    def test_inactive_does_not_touch_source_health_history(self):
        event_id = _insert_event(
            self.conn, consecutive_failures=4, last_check_status="unavailable",
            last_checked="2026-09-01T00:00:00+00:00", last_successful_check="2026-08-01T00:00:00+00:00",
        )
        self._post(f"/event/{event_id}/inactive")
        row = self._event(event_id)
        self.assertEqual(row["consecutive_failures"], 4)
        self.assertEqual(row["last_check_status"], "unavailable")
        self.assertEqual(row["last_checked"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(row["last_successful_check"], "2026-08-01T00:00:00+00:00")

    def test_get_inactive_is_not_allowed(self):
        event_id = _insert_event(self.conn)
        resp = self.client.get(f"/event/{event_id}/inactive")
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(self._event(event_id)["event_state"], "active")


class ReactivateRouteTests(AppTestCase):
    def test_post_reactivate_restores_active(self):
        event_id = _insert_event(self.conn, event_state="inactive", inactive_reason="manual")
        resp = self._post(f"/event/{event_id}/reactivate")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id)["event_state"], "active")

    def test_reactivate_clears_inactive_reason(self):
        event_id = _insert_event(self.conn, event_state="inactive", inactive_reason="canceled")
        self._post(f"/event/{event_id}/reactivate")
        self.assertIsNone(self._event(event_id)["inactive_reason"])

    def test_reactivate_preserves_source_health_history(self):
        event_id = _insert_event(
            self.conn, event_state="inactive", inactive_reason="manual",
            consecutive_failures=3, last_check_status="not_found",
            last_checked="2026-09-01T00:00:00+00:00", last_successful_check="2026-08-01T00:00:00+00:00",
        )
        self._post(f"/event/{event_id}/reactivate")
        row = self._event(event_id)
        self.assertEqual(row["consecutive_failures"], 3)
        self.assertEqual(row["last_check_status"], "not_found")
        self.assertEqual(row["last_checked"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(row["last_successful_check"], "2026-08-01T00:00:00+00:00")

    def test_get_reactivate_is_not_allowed(self):
        event_id = _insert_event(self.conn, event_state="inactive")
        resp = self.client.get(f"/event/{event_id}/reactivate")
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(self._event(event_id)["event_state"], "inactive")


class SourceRecoveryDashboardTests(AppTestCase):
    def test_pending_candidate_renders(self):
        event_id = _insert_event(self.conn, name="Flaky Hack", consecutive_failures=3)
        _insert_recovery_candidate(self.conn, event_id)
        html = self.client.get("/").data.decode()
        self.assertIn("Possible replacement source", html)
        self.assertIn("MLH", html)
        self.assertIn("https://mlh.io/events/test", html)

    def test_accepted_candidate_hidden(self):
        event_id = _insert_event(self.conn, consecutive_failures=3)
        _insert_recovery_candidate(self.conn, event_id, status="accepted")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Possible replacement source", html)

    def test_dismissed_candidate_hidden(self):
        event_id = _insert_event(self.conn, consecutive_failures=3)
        _insert_recovery_candidate(self.conn, event_id, status="dismissed")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Possible replacement source", html)


class SourceRecoveryRouteTests(AppTestCase):
    def test_accept_post_updates_source_url(self):
        event_id = _insert_event(self.conn, source_url="https://old.example.com", consecutive_failures=3)
        cid = _insert_recovery_candidate(self.conn, event_id, candidate_url="https://mlh.io/events/new")
        resp = self._post(f"/source-recovery/{cid}/accept")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id)["source_url"], "https://mlh.io/events/new")
        self.assertEqual(self._recovery_candidate(cid)["status"], "accepted")

    def test_dismiss_post_leaves_source_url_unchanged(self):
        event_id = _insert_event(self.conn, source_url="https://old.example.com", consecutive_failures=3)
        cid = _insert_recovery_candidate(self.conn, event_id, candidate_url="https://mlh.io/events/new")
        resp = self._post(f"/source-recovery/{cid}/dismiss")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id)["source_url"], "https://old.example.com")
        self.assertEqual(self._recovery_candidate(cid)["status"], "dismissed")

    def test_nonexistent_candidate_does_not_crash(self):
        resp = self._post("/source-recovery/99999/accept")
        self.assertEqual(resp.status_code, 200)

    def test_already_resolved_candidate_does_not_crash(self):
        event_id = _insert_event(self.conn, consecutive_failures=3)
        cid = _insert_recovery_candidate(self.conn, event_id, status="accepted")
        resp = self._post(f"/source-recovery/{cid}/accept")
        self.assertEqual(resp.status_code, 200)

    def test_get_accept_is_not_allowed(self):
        event_id = _insert_event(self.conn, consecutive_failures=3)
        cid = _insert_recovery_candidate(self.conn, event_id)
        resp = self.client.get(f"/source-recovery/{cid}/accept")
        self.assertEqual(resp.status_code, 405)

    def test_get_dismiss_is_not_allowed(self):
        event_id = _insert_event(self.conn, consecutive_failures=3)
        cid = _insert_recovery_candidate(self.conn, event_id)
        resp = self.client.get(f"/source-recovery/{cid}/dismiss")
        self.assertEqual(resp.status_code, 405)


class AsuDashboardTests(AppTestCase):
    def test_asu_badge_shown(self):
        _insert_event(self.conn, name="Discovery Day with Viasat", event_type="asu")
        html = self.client.get("/").data.decode()
        self.assertIn("badge-asu", html)
        self.assertIn(">ASU<", html)

    def test_subtype_visible(self):
        _insert_event(self.conn, name="Discovery Day with Viasat", event_type="asu", event_subtype="employer_event")
        html = self.client.get("/").data.decode()
        self.assertIn("Employer Event", html)

    def test_location_area_visible(self):
        _insert_event(self.conn, name="Resume Workshop", event_type="asu", location_area="tempe")
        html = self.client.get("/").data.decode()
        self.assertIn("Tempe", html)

    def test_non_asu_event_has_no_asu_badge(self):
        _insert_event(self.conn, name="MHacks", event_type="hackathon")
        html = self.client.get("/").data.decode()
        self.assertNotIn("badge-asu", html)

    def test_existing_status_controls_still_work_for_asu_event(self):
        event_id = _insert_event(self.conn, event_type="asu")
        resp = self._post(f"/event/{event_id}/interested")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._owner_status(event_id), "interested")
        self.assertEqual(self._event(event_id)["status"], "new")  # legacy untouched


class CalendarLifecycleRegressionTests(AppTestCase):
    def test_interested_active_event_appears_in_ics(self):
        _insert_event(self.conn, name="Career Fair", status="interested", event_state="active")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("SUMMARY:Career Fair", ics)

    def test_interested_inactive_event_excluded_from_ics(self):
        _insert_event(self.conn, name="Canceled Fair", status="interested", event_state="inactive")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Canceled Fair", ics)

    def test_marking_inactive_preserves_interested_status(self):
        event_id = _insert_event(self.conn, name="Career Fair", status="interested", event_state="active")
        self._post(f"/event/{event_id}/inactive")
        self.assertEqual(self._owner_status(event_id), "interested")
        self.assertEqual(self._event(event_id)["event_state"], "inactive")

    def test_marking_inactive_removes_event_from_ics(self):
        event_id = _insert_event(self.conn, name="Career Fair", status="interested", event_state="active")
        self._post(f"/event/{event_id}/inactive")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Career Fair", ics)

    def test_reactivating_returns_event_to_ics(self):
        event_id = _insert_event(self.conn, name="Career Fair", status="interested", event_state="inactive")
        ics_before = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Career Fair", ics_before)

        self._post(f"/event/{event_id}/reactivate")
        self.assertEqual(self._event(event_id)["status"], "interested")
        ics_after = self.client.get("/calendar.ics").data.decode()
        self.assertIn("SUMMARY:Career Fair", ics_after)

    def test_tba_no_date_event_still_excluded(self):
        _insert_event(self.conn, name="TBA Event", status="interested", event_state="active",
                       date_status="tba", start_date=None)
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("TBA Event", ics)

    def test_non_interested_active_event_excluded(self):
        _insert_event(self.conn, name="Just Watching", status="watching", event_state="active")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Just Watching", ics)

    def test_genuine_all_day_event_still_renders_as_all_day(self):
        # start_time=None here represents a real all-day event (e.g. a
        # hackathon with only a date) - unrelated to the ASU discovery-time
        # validation, which prevents this state from ever being *inserted*
        # for an ASU candidate with incomplete time evidence. Once a row
        # legitimately has start_time=None, ICS rendering must still treat
        # it as all-day.
        _insert_event(self.conn, name="Local Hack Day", status="interested", event_state="active", start_time=None)
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("SUMMARY:Local Hack Day", ics)
        self.assertIn("DTSTART;VALUE=DATE:", ics)


class ReviewQueueDashboardTests(AppTestCase):
    def test_pending_review_candidates_visible(self):
        _insert_review_candidate(self.conn, name="Maosho")
        html = self.client.get("/").data.decode()
        self.assertIn("Events to Review", html)
        self.assertIn("Maosho", html)

    def test_accepted_review_candidate_hidden(self):
        _insert_review_candidate(self.conn, name="Maosho", status="accepted")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Events to Review", html)

    def test_dismissed_review_candidate_hidden(self):
        _insert_review_candidate(self.conn, name="Maosho", status="dismissed")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Events to Review", html)

    def test_reason_and_signals_visible(self):
        _insert_review_candidate(
            self.conn, review_reason="no clear include or exclude evidence",
            review_signals=json.dumps(["supporting_category:science"]),
        )
        html = self.client.get("/").data.decode()
        self.assertIn("no clear include or exclude evidence", html)
        self.assertIn("supporting_category:science", html)

    def test_source_link_visible(self):
        _insert_review_candidate(self.conn, source_url="https://asuevents.asu.edu/event/maosho")
        html = self.client.get("/").data.decode()
        self.assertIn("https://asuevents.asu.edu/event/maosho", html)


class ReviewRouteTests(AppTestCase):
    def test_add_event_post_works(self):
        review_id = _insert_review_candidate(self.conn, name="Maosho")
        resp = self._post(f"/review/{review_id}/accept")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._review_candidate(review_id)["status"], "accepted")
        row = self.conn.execute("SELECT * FROM events WHERE name = 'Maosho'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["event_state"], "active")

    def test_dismiss_post_works(self):
        review_id = _insert_review_candidate(self.conn, name="Maosho")
        resp = self._post(f"/review/{review_id}/dismiss")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._review_candidate(review_id)["status"], "dismissed")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_nonexistent_review_id_does_not_crash(self):
        resp = self._post("/review/99999/accept")
        self.assertEqual(resp.status_code, 200)

    def test_already_resolved_review_does_not_crash(self):
        review_id = _insert_review_candidate(self.conn, status="accepted")
        resp = self._post(f"/review/{review_id}/accept")
        self.assertEqual(resp.status_code, 200)

    def test_get_accept_is_not_allowed(self):
        review_id = _insert_review_candidate(self.conn)
        resp = self.client.get(f"/review/{review_id}/accept")
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(self._review_candidate(review_id)["status"], "pending")

    def test_get_dismiss_is_not_allowed(self):
        review_id = _insert_review_candidate(self.conn)
        resp = self.client.get(f"/review/{review_id}/dismiss")
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(self._review_candidate(review_id)["status"], "pending")


class ReviewCalendarRegressionTests(AppTestCase):
    def test_pending_review_candidate_absent_from_ics(self):
        _insert_review_candidate(self.conn, name="Maosho")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Maosho", ics)

    def test_newly_accepted_status_new_event_absent_from_ics(self):
        review_id = _insert_review_candidate(self.conn, name="Maosho")
        self._post(f"/review/{review_id}/accept")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Maosho", ics)

    def test_only_interested_active_event_appears_after_manual_interested(self):
        review_id = _insert_review_candidate(self.conn, name="Maosho")
        self._post(f"/review/{review_id}/accept")
        row = self.conn.execute("SELECT id FROM events WHERE name = 'Maosho'").fetchone()
        self._post(f"/event/{row['id']}/interested")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("SUMMARY:Maosho", ics)


class RecurringSeriesCalendarGuardTests(AppTestCase):
    """V0.9B.1 defensive regression: calendar_service must never silently
    turn a recurring/multi-occurrence series data mistake into an enormous
    timed event - without adding an arbitrary duration cap that would
    break a legitimate multi-day event. The real fix is in the normalizer
    (it now stores end_date=None for a detected recurring series); these
    tests prove the calendar layer renders that correctly, and that
    genuine multi-day/same-day/all-day events are all unaffected."""

    def test_same_day_timed_event_emits_correct_dtstart_dtend(self):
        _insert_event(
            self.conn, name="Same Day Talk", status="interested", event_state="active",
            start_date="2026-09-08", end_date="2026-09-08", start_time="10:00", end_time="11:00",
            timezone="America/Phoenix",
        )
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("DTSTART:20260908T170000Z", ics)
        self.assertIn("DTEND:20260908T180000Z", ics)

    def test_genuine_multi_day_timed_event_still_spans_correctly(self):
        # "One-on-One Career Chats with Gray Media" real shape - a
        # legitimate 2-day event must keep spanning both days.
        _insert_event(
            self.conn, name="Two Day Chat", status="interested", event_state="active",
            start_date="2026-09-09", end_date="2026-09-10", start_time="09:00", end_time="17:00",
            timezone="America/Phoenix",
        )
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("DTSTART:20260909T160000Z", ics)
        self.assertIn("DTEND:20260911T000000Z", ics)

    def test_recurring_series_representation_never_produces_giant_span(self):
        # The normalizer now stores end_date=None for a detected recurring
        # series (see test_asu_event_normalizer.RecurringSeriesDateRangeTests) -
        # prove the calendar layer renders that as a short single-day
        # entry, not the multi-month span the raw source data implied,
        # even though calendar_service itself has no duration cap.
        _insert_event(
            self.conn, name="Poly Career Advising Drop-In's", status="interested", event_state="active",
            start_date="2026-08-26", end_date=None, start_time="12:00", end_time="14:00",
            timezone="America/Phoenix",
        )
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("DTSTART:20260826T190000Z", ics)
        self.assertIn("DTEND:20260826T210000Z", ics)
        self.assertNotIn("20261202", ics)

    def test_all_day_behavior_unchanged(self):
        _insert_event(
            self.conn, name="All Day Hack", status="interested", event_state="active",
            start_date="2026-09-05", end_date="2026-09-07", start_time=None,
        )
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("DTSTART;VALUE=DATE:20260905", ics)
        self.assertIn("DTEND;VALUE=DATE:20260908", ics)


class AcceptedAllDayReviewCalendarTests(AppTestCase):
    """V0.9B.2: an explicit all-day REVIEW candidate must accept into a
    genuinely all-day event and render correctly once marked Interested -
    proving the persisted all_day column round-trips through accept."""

    def test_accepted_all_day_review_renders_as_all_day_once_interested(self):
        review_id = _insert_review_candidate(
            self.conn, name="All Day Fair", start_time=None, end_time=None, timezone=None, all_day=1,
        )
        self._post(f"/review/{review_id}/accept")
        row = self.conn.execute("SELECT id FROM events WHERE name = 'All Day Fair'").fetchone()
        self.assertIsNotNone(row)

        self._post(f"/event/{row['id']}/interested")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("SUMMARY:All Day Fair", ics)
        self.assertIn("DTSTART;VALUE=DATE:20260915", ics)


class PerUserStatusDashboardTests(AppTestCase):
    """V0.10B: the dashboard's `status` field for the bootstrap owner now
    comes from user_event_status, not the legacy events.status column."""

    def test_owner_interested_status_shown(self):
        event_id = _insert_event(self.conn, name="Career Fair")
        self._set_owner_status(event_id, "interested")
        html = self.client.get("/").data.decode()
        self.assertIn("badge-interested", html)

    def test_owner_watching_status_shown(self):
        event_id = _insert_event(self.conn, name="Career Fair")
        self._set_owner_status(event_id, "watching")
        html = self.client.get("/").data.decode()
        self.assertIn("badge-watching", html)

    def test_owner_ignored_status_shown(self):
        event_id = _insert_event(self.conn, name="Career Fair")
        self._set_owner_status(event_id, "ignored")
        html = self.client.get("/").data.decode()
        self.assertIn("badge-ignored", html)

    def test_missing_status_row_shown_as_new(self):
        _insert_event(self.conn, name="Career Fair")  # default status="new", no owner row created
        html = self.client.get("/").data.decode()
        self.assertIn("badge-new", html)

    def test_dashboard_ordering_uses_effective_status_not_legacy(self):
        # Legacy events.status='ignored' but the owner's real per-user
        # status is interested - ignored events sort last, so if ordering
        # used the legacy column this would wrongly sort after a
        # genuinely-new event instead of before it.
        effectively_interested = _insert_event(self.conn, name="AAA Effectively Interested")
        self.conn.execute("UPDATE events SET status = 'ignored' WHERE id = ?", (effectively_interested,))
        self.conn.commit()
        self._set_owner_status(effectively_interested, "interested")

        _insert_event(self.conn, name="ZZZ Plain New")

        html = self.client.get("/").data.decode()
        self.assertLess(html.index("AAA Effectively Interested"), html.index("ZZZ Plain New"))

    def test_conflicting_legacy_and_per_user_status_new_source_wins(self):
        event_id = _insert_event(self.conn, name="Conflict Event")
        self.conn.execute("UPDATE events SET status = 'ignored' WHERE id = ?", (event_id,))
        self.conn.commit()
        self._set_owner_status(event_id, "interested")

        html = self.client.get("/").data.decode()
        self.assertIn("badge-interested", html)
        self.assertNotIn("badge-ignored", html)


class StatusActionUserEventStatusTests(AppTestCase):
    """V0.10B: Interested/Watching/Ignore write only user_event_status."""

    def test_interested_writes_user_event_status(self):
        event_id = _insert_event(self.conn)
        self._post(f"/event/{event_id}/interested")
        self.assertEqual(self._owner_status(event_id), "interested")

    def test_watching_writes_user_event_status(self):
        event_id = _insert_event(self.conn)
        self._post(f"/event/{event_id}/watching")
        self.assertEqual(self._owner_status(event_id), "watching")

    def test_ignore_writes_user_event_status(self):
        event_id = _insert_event(self.conn)
        self._post(f"/event/{event_id}/ignore")
        self.assertEqual(self._owner_status(event_id), "ignored")

    def test_existing_per_user_row_updates_without_duplicating(self):
        event_id = _insert_event(self.conn)
        self._post(f"/event/{event_id}/interested")
        self._post(f"/event/{event_id}/watching")
        self.assertEqual(self._owner_status(event_id), "watching")
        count = self.conn.execute(
            "SELECT COUNT(*) FROM user_event_status WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_events_status_never_modified_by_any_action(self):
        event_id = _insert_event(self.conn)
        for action in ("interested", "watching", "ignore"):
            self._post(f"/event/{event_id}/{action}")
            self.assertEqual(self._event(event_id)["status"], "new")

    def test_invalid_action_returns_404(self):
        event_id = _insert_event(self.conn)
        resp = self._post(f"/event/{event_id}/bogus")
        self.assertEqual(resp.status_code, 404)

    def test_nonexistent_event_action_does_not_crash(self):
        resp = self._post("/event/99999/interested")
        self.assertEqual(resp.status_code, 302)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM user_event_status WHERE event_id = 99999"
        ).fetchone()[0]
        self.assertEqual(count, 0)


class PerUserCalendarConflictTests(AppTestCase):
    """V0.10B: /calendar.ics must reflect the bootstrap owner's
    user_event_status, never the legacy events.status column."""

    def test_legacy_ignored_but_owner_interested_appears(self):
        event_id = _insert_event(self.conn, name="Career Fair", event_state="active")
        self.conn.execute("UPDATE events SET status = 'ignored' WHERE id = ?", (event_id,))
        self.conn.commit()
        self._set_owner_status(event_id, "interested")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertIn("SUMMARY:Career Fair", ics)

    def test_legacy_interested_but_owner_ignored_excluded(self):
        event_id = _insert_event(self.conn, name="Career Fair", status="interested", event_state="active")
        self._set_owner_status(event_id, "ignored")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Career Fair", ics)

    def test_legacy_interested_but_no_owner_row_excluded(self):
        event_id = _insert_event(self.conn, name="Career Fair", event_state="active")
        self.conn.execute("UPDATE events SET status = 'interested' WHERE id = ?", (event_id,))
        self.conn.commit()
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Career Fair", ics)

    def test_owner_interested_but_event_inactive_excluded(self):
        event_id = _insert_event(self.conn, name="Career Fair", event_state="inactive")
        self._set_owner_status(event_id, "interested")
        ics = self.client.get("/calendar.ics").data.decode()
        self.assertNotIn("Career Fair", ics)


def _create_friend(conn, email="friend@example.com", password="friend-test-password"):
    user = create_user(conn, email, password)
    conn.commit()
    return user


class AuthTests(AppTestCase):
    """V0.10C: login/logout behavior. AppTestCase.setUp already logs in
    as the owner with OWNER_TEST_PASSWORD - most tests here log out first."""

    def test_valid_login_succeeds(self):
        self._logout()
        resp = self._login(self.owner["email"], OWNER_TEST_PASSWORD)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_wrong_password_generic_failure(self):
        self._logout()
        resp = self._login(self.owner["email"], "totally-wrong-password")
        self.assertEqual(resp.status_code, 401)
        self.assertIn(b"Invalid email or password.", resp.data)

    def test_nonexistent_email_same_generic_failure(self):
        self._logout()
        resp = self._login("nobody@example.com", "whatever")
        self.assertEqual(resp.status_code, 401)
        self.assertIn(b"Invalid email or password.", resp.data)

    def test_null_password_hash_account_cannot_login(self):
        self._logout()
        now = "2026-09-06T00:00:00+00:00"
        self.conn.execute(
            "INSERT INTO users (email, display_name, password_hash, is_admin, created_at, updated_at) "
            "VALUES ('unset@example.com', 'Unset', NULL, 0, ?, ?)", (now, now),
        )
        self.conn.commit()
        resp = self._login("unset@example.com", "anything")
        self.assertEqual(resp.status_code, 401)
        self.assertIn(b"Invalid email or password.", resp.data)

    def test_email_case_and_whitespace_normalized(self):
        self._logout()
        resp = self._login(f"  {self.owner['email'].upper()}  ", OWNER_TEST_PASSWORD)
        self.assertEqual(resp.status_code, 302)

    def test_malicious_next_url_not_followed(self):
        self._logout()
        token = self._csrf_token()
        resp = self.client.post("/login", data={
            "email": self.owner["email"], "password": OWNER_TEST_PASSWORD,
            "next": "https://evil.example.com/", "csrf_token": token,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("evil.example.com", resp.headers["Location"])

    def test_get_logout_not_allowed(self):
        resp = self.client.get("/logout")
        self.assertEqual(resp.status_code, 405)

    def test_logout_clears_session(self):
        self._logout()
        resp = self.client.get("/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_invalid_csrf_does_not_logout(self):
        resp = self.client.post("/logout", data={"csrf_token": "wrong-token"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.client.get("/").status_code, 200)  # still logged in

    def test_already_authenticated_visiting_login_redirects(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 302)


class CsrfTests(AppTestCase):
    """V0.10C: CSRF is enforced for real (session_transaction sets the
    session's real token; these tests never disable the check)."""

    def test_status_post_without_token_rejected_no_mutation(self):
        event_id = _insert_event(self.conn)
        resp = self.client.post(f"/event/{event_id}/interested", data={})
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(self._owner_status(event_id))

    def test_status_post_wrong_token_rejected_no_mutation(self):
        event_id = _insert_event(self.conn)
        resp = self.client.post(f"/event/{event_id}/interested", data={"csrf_token": "wrong-token"})
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(self._owner_status(event_id))

    def test_status_post_valid_token_succeeds(self):
        event_id = _insert_event(self.conn)
        resp = self._post(f"/event/{event_id}/interested")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._owner_status(event_id), "interested")

    def test_admin_post_without_token_rejected_no_mutation(self):
        event_id = _insert_event(self.conn, event_state="active")
        resp = self.client.post(f"/event/{event_id}/inactive", data={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._event(event_id)["event_state"], "active")

    def test_login_post_without_token_rejected(self):
        self._logout()
        resp = self.client.post("/login", data={"email": self.owner["email"], "password": OWNER_TEST_PASSWORD})
        self.assertEqual(resp.status_code, 400)


class AdminAuthorizationTests(AppTestCase):
    """V0.10C: server-side enforcement of ADMIN ONLY shared-management
    routes - never enforced only by hiding a template button."""

    def setUp(self):
        super().setUp()
        self.friend = _create_friend(self.conn)

    def _login_as_friend(self):
        self._logout()
        self._login(self.friend["email"], "friend-test-password")

    def test_unauthenticated_dashboard_redirects_to_login(self):
        self._logout()
        resp = self.client.get("/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_unauthenticated_status_post_blocked_no_mutation(self):
        event_id = _insert_event(self.conn)
        self._logout()
        resp = self._post(f"/event/{event_id}/interested")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.assertIsNone(get_user_event_status(self.conn, self.owner["id"], event_id))

    def test_unauthenticated_admin_action_redirects_to_login(self):
        event_id = _insert_event(self.conn, event_state="active")
        self._logout()
        resp = self._post(f"/event/{event_id}/inactive")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_regular_user_dashboard_allowed(self):
        self._login_as_friend()
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_regular_user_own_status_mutation_allowed(self):
        event_id = _insert_event(self.conn)
        self._login_as_friend()
        resp = self._post(f"/event/{event_id}/interested")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(get_user_event_status(self.conn, self.friend["id"], event_id), "interested")

    def test_regular_user_admin_post_403_no_mutation(self):
        event_id = _insert_event(self.conn, event_state="active")
        self._login_as_friend()
        resp = self._post(f"/event/{event_id}/inactive")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._event(event_id)["event_state"], "active")

    def test_regular_user_cannot_accept_review_candidate(self):
        review_id = _insert_review_candidate(self.conn, name="Maosho")
        self._login_as_friend()
        resp = self._post(f"/review/{review_id}/accept")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._review_candidate(review_id)["status"], "pending")

    def test_regular_user_cannot_accept_proposal(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        self._login_as_friend()
        resp = self._post(f"/proposal/{pid}/accept")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._proposal(pid)["status"], "pending")

    def test_regular_user_cannot_accept_source_recovery(self):
        event_id = _insert_event(self.conn, consecutive_failures=3)
        cid = _insert_recovery_candidate(self.conn, event_id)
        self._login_as_friend()
        resp = self._post(f"/source-recovery/{cid}/accept")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._recovery_candidate(cid)["status"], "pending")

    def test_admin_retains_full_functionality(self):
        # still logged in as owner/admin (AppTestCase default)
        event_id = _insert_event(self.conn, event_state="active")
        resp = self._post(f"/event/{event_id}/inactive")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id)["event_state"], "inactive")


class DashboardIsolationTests(AppTestCase):
    """V0.10C requirement: the same shared event can hold different
    statuses for different users without interfering with one another."""

    def setUp(self):
        super().setUp()
        self.friend = _create_friend(self.conn)

    def _login_as_friend(self):
        self._logout()
        self._login(self.friend["email"], "friend-test-password")

    def test_owner_interested_visible_to_owner(self):
        event_id = _insert_event(self.conn, name="Shared Event")
        self._post(f"/event/{event_id}/interested")
        html = self.client.get("/").data.decode()
        self.assertIn("badge-interested", html)

    def test_friend_sees_new_when_no_row(self):
        event_id = _insert_event(self.conn, name="Shared Event")
        self._post(f"/event/{event_id}/interested")  # owner
        self._login_as_friend()
        html = self.client.get("/").data.decode()
        self.assertIn("badge-new", html)
        self.assertNotIn("badge-interested", html)

    def test_friend_changing_status_does_not_affect_owner(self):
        event_id = _insert_event(self.conn, name="Shared Event")
        self._post(f"/event/{event_id}/interested")  # owner
        self._login_as_friend()
        self._post(f"/event/{event_id}/watching")  # friend

        self.assertEqual(get_user_event_status(self.conn, self.owner["id"], event_id), "interested")
        self.assertEqual(get_user_event_status(self.conn, self.friend["id"], event_id), "watching")

    def test_legacy_events_status_irrelevant_for_either_user(self):
        event_id = _insert_event(self.conn, name="Shared Event")
        self.conn.execute("UPDATE events SET status = 'ignored' WHERE id = ?", (event_id,))
        self.conn.commit()
        self._post(f"/event/{event_id}/interested")  # owner, via the real route
        self.assertEqual(self._event(event_id)["status"], "ignored")  # legacy untouched
        self.assertEqual(get_user_event_status(self.conn, self.owner["id"], event_id), "interested")

    def test_shared_event_not_duplicated_across_users(self):
        event_id = _insert_event(self.conn, name="Shared Event")
        self._post(f"/event/{event_id}/interested")
        self._login_as_friend()
        self._post(f"/event/{event_id}/watching")
        count = self.conn.execute("SELECT COUNT(*) FROM events WHERE name = 'Shared Event'").fetchone()[0]
        self.assertEqual(count, 1)


class SecretKeyStartupTests(unittest.TestCase):
    """V0.10C: app.py must fail immediately at import/startup - not
    lazily on first session touch - if EVENT_RADAR_SECRET_KEY is unset.
    Runs app.py import in a real subprocess (the in-process `app_module`
    is already imported once at the top of this file, so this is the
    only way to exercise import-time behavior). Always redirects
    db.DB_PATH to a throwaway file before importing app, in the same
    subprocess, so init_db() never touches the real data/events.db even
    on the success path."""

    _REDIRECT_DB_PATH = (
        "import db, tempfile, pathlib; "
        "db.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / 'events.db'; "
    )

    def _import_app_in_subprocess(self, secret_key):
        env = dict(os.environ)
        if secret_key is None:
            env.pop("EVENT_RADAR_SECRET_KEY", None)
        else:
            env["EVENT_RADAR_SECRET_KEY"] = secret_key
        return subprocess.run(
            [sys.executable, "-c", self._REDIRECT_DB_PATH + "import app"],
            cwd=str(Path(__file__).parent), env=env, capture_output=True, text=True,
        )

    def test_import_fails_clearly_without_secret_key(self):
        result = self._import_app_in_subprocess(secret_key=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EVENT_RADAR_SECRET_KEY", result.stderr)

    def test_import_succeeds_with_secret_key_set(self):
        result = self._import_app_in_subprocess(secret_key="a-throwaway-subprocess-test-secret")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
