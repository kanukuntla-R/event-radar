"""Flask dashboard tests for the proposal review UI (V0.4C). Runs entirely
against temporary SQLite files (db.DB_PATH monkeypatched) - never touches the
real data/events.db. Stdlib unittest only.

Run with: python -m unittest test_app -v
"""
import tempfile
import unittest
from pathlib import Path

import db as db_module

# app.py calls init_db() at import time, so db.DB_PATH must already point at
# a throwaway file *before* app is first imported anywhere in this process -
# otherwise that first import would create/touch the real data/events.db.
_import_tmpdir = tempfile.TemporaryDirectory()
db_module.DB_PATH = Path(_import_tmpdir.name) / "import-events.db"

import app as app_module  # noqa: E402


def _insert_event(conn, **overrides):
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
    return cur.lastrowid


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


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self._tmpdir.name) / "events.db"
        app_module.init_db()
        self.client = app_module.app.test_client()
        self.conn = db_module.get_db()

    def tearDown(self):
        self.conn.close()
        self._tmpdir.cleanup()

    def _proposal(self, proposal_id):
        return self.conn.execute(
            "SELECT * FROM event_change_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()

    def _event(self, event_id):
        return self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


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
        resp = self.client.post(f"/proposal/{pid}/accept")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id)["registration_status"], "open")
        self.assertEqual(self._proposal(pid)["status"], "accepted")

    def test_accepted_proposal_disappears_from_pending_ui(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        self.client.post(f"/proposal/{pid}/accept")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Detected changes", html)

    def test_stale_accept_handled_gracefully(self):
        event_id = _insert_event(self.conn, registration_status="closed")
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        resp = self.client.post(f"/proposal/{pid}/accept")
        self.assertEqual(resp.status_code, 200)  # rendered dashboard, not a 500
        self.assertEqual(self._event(event_id)["registration_status"], "closed")
        self.assertEqual(self._proposal(pid)["status"], "pending")

    def test_nonexistent_proposal_does_not_crash(self):
        resp = self.client.post("/proposal/99999/accept")
        self.assertEqual(resp.status_code, 200)

    def test_already_resolved_proposal_accept_does_not_crash(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open", status="accepted")
        resp = self.client.post(f"/proposal/{pid}/accept")
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
        resp = self.client.post(f"/proposal/{pid}/dismiss")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id), before)
        self.assertEqual(self._proposal(pid)["status"], "dismissed")

    def test_dismissed_proposal_disappears_from_pending_ui(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        self.client.post(f"/proposal/{pid}/dismiss")
        html = self.client.get("/").data.decode()
        self.assertNotIn("Detected changes", html)

    def test_nonexistent_proposal_dismiss_does_not_crash(self):
        resp = self.client.post("/proposal/99999/dismiss")
        self.assertEqual(resp.status_code, 200)

    def test_dismiss_via_get_is_not_allowed(self):
        event_id = _insert_event(self.conn)
        pid = _insert_proposal(self.conn, event_id, "registration_status", "unknown", "open")
        resp = self.client.get(f"/proposal/{pid}/dismiss")
        self.assertEqual(resp.status_code, 405)


class ExistingBehaviorRegressionTests(AppTestCase):
    def test_event_status_controls_still_work(self):
        event_id = _insert_event(self.conn)
        resp = self.client.post(f"/event/{event_id}/interested")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._event(event_id)["status"], "interested")

    def test_dashboard_renders_with_no_proposals(self):
        _insert_event(self.conn)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Detected changes", resp.data)


if __name__ == "__main__":
    unittest.main()
