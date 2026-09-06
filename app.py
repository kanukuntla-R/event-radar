import json
import os
from datetime import datetime

from flask import Flask, render_template, redirect, url_for, Response, abort, request, session, g

from db import get_db, init_db
from calendar_service import generate_ics
from proposal_resolver import accept_proposal, dismiss_proposal, StaleProposalError
from source_health import classify_source_health
from source_recovery_resolver import accept_recovery_candidate, dismiss_recovery_candidate
from event_review_resolver import accept_review_candidate, dismiss_review_candidate
from user_store import get_or_create_bootstrap_owner, set_user_event_status, verify_user_password
from auth import (
    load_current_user, login_required, admin_required,
    generate_csrf_token, enforce_csrf, is_safe_next_url,
)

# Explicit column list for the dashboard query - events.status is legacy
# single-user state (V0.10A/B) and must NOT be selected alongside the
# COALESCE(...) AS status alias below, since sqlite3.Row would then have
# two columns literally named "status" and indexing by name returns
# whichever comes first (the wrong, legacy one).
_EVENT_COLUMNS_EXCEPT_LEGACY_STATUS = ", ".join(
    f"e.{col}" for col in (
        "id", "name", "event_type", "description", "start_date", "end_date",
        "start_time", "end_time", "timezone", "date_status", "location",
        "location_type", "source", "source_url", "organizer",
        "registration_status", "registration_url", "application_deadline",
        "last_checked", "travel_required", "event_state", "inactive_reason",
        "last_check_status", "consecutive_failures", "last_successful_check",
        "event_subtype", "location_area", "first_seen", "updated_at",
    )
)

app = Flask(__name__)

# V0.10C: session signing key. Must be a durable, cryptographically random
# value from the environment - never hardcoded, never regenerated per
# process start (that would invalidate every session on every restart),
# and never given a development fallback here (a fallback would make it
# trivially easy to accidentally run "production" with a guessable key).
# Fails immediately at import/startup if unset, rather than letting
# Gunicorn boot successfully and only failing deep in Flask's session
# machinery on the first request that touches it - see
# deploy/generate-secret.sh and deploy/systemd/README.md for how the live
# service provides this via an EnvironmentFile. Tests set this same
# environment variable to a fixed test value *before* importing this
# module (see test_app.py) - exactly like a real deployment, just with a
# throwaway value.
app.secret_key = os.environ.get("EVENT_RADAR_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError(
        "EVENT_RADAR_SECRET_KEY is not set. Flask session signing needs a "
        "persistent secret - see deploy/generate-secret.sh and "
        "deploy/systemd/README.md. Refusing to start."
    )

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Current deployment is HTTP over trusted Tailscale/LAN only - forcing
# Secure here would make the browser stop sending the cookie entirely.
# MUST be set to true (EVENT_RADAR_FORCE_SECURE_COOKIES=1) before any
# HTTPS/public-Cloudflare deployment.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("EVENT_RADAR_FORCE_SECURE_COOKIES") == "1"

app.before_request(load_current_user)
app.before_request(enforce_csrf)
app.jinja_env.globals["csrf_token"] = generate_csrf_token

ACTION_TO_STATUS = {
    "interested": "interested",
    "watching": "watching",
    "ignore": "ignored",
}

INACTIVE_REASONS = {"canceled", "removed", "source_missing", "finished", "manual"}

FIELD_LABELS = {
    "start_date": "Start date",
    "end_date": "End date",
    "date_status": "Date status",
    "registration_status": "Registration status",
    "registration_url": "Registration URL",
    "application_deadline": "Application deadline",
}

CANDIDATE_TYPE_LABELS = {
    "official_event": "official event",
    "official_organizer": "official organizer",
    "official_university": "official university",
    "mlh": "MLH",
    "official_social": "official social",
    "other": "other",
}

init_db()


@app.template_filter("field_label")
def field_label(field):
    return FIELD_LABELS.get(field, field)


@app.template_filter("proposal_value")
def format_proposal_value(value):
    if value in (None, ""):
        return "Not set"
    text = str(value).replace("_", " ")
    return text if "://" in text else text[:1].upper() + text[1:]


@app.template_filter("source_health")
def source_health_filter(e):
    return classify_source_health(e)


@app.template_filter("candidate_type_label")
def candidate_type_label(candidate_type):
    return CANDIDATE_TYPE_LABELS.get(candidate_type, candidate_type)


@app.template_filter("subtype_label")
def subtype_label(subtype):
    return (subtype or "").replace("_", " ").title()


@app.template_filter("review_signals_list")
def review_signals_list(raw_json):
    try:
        return json.loads(raw_json) if raw_json else []
    except (TypeError, ValueError):
        return []


def _fmt_date(d):
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _fmt_time(t):
    return t.strftime("%I:%M %p").lstrip("0")


def _format_date_time_label(start_date_str, start_time_str, end_date_str, end_time_str):
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    date_label = _fmt_date(start_date)

    if start_time_str:
        start_time = datetime.strptime(start_time_str, "%H:%M")
        label = f"{date_label} • {_fmt_time(start_time)}"
        if end_time_str:
            end_time = datetime.strptime(end_time_str, "%H:%M")
            label += f" – {_fmt_time(end_time)}"
        return label

    if end_date_str and end_date_str != start_date_str:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        return f"{date_label} – {_fmt_date(end_date)}"

    return date_label


@app.template_filter("event_date")
def format_event_date(e):
    if e["date_status"] == "tba" or not e["start_date"]:
        return "TBA"
    return _format_date_time_label(e["start_date"], e["start_time"], e["end_date"], e["end_time"])


@app.template_filter("review_date")
def format_review_date(row):
    # event_review_candidates has no date_status column - a present
    # start_date is treated as confirmed, same as the events table default.
    if not row["start_date"]:
        return "Date TBA"
    return _format_date_time_label(row["start_date"], row["start_time"], row["end_date"], row["end_time"])


def _load_dashboard_events(conn, user_id):
    """V0.10C: `status` comes from user_event_status.status for the
    CURRENTLY AUTHENTICATED user - never the bootstrap owner, never the
    legacy events.status column. A missing row (an event this user hasn't
    acted on yet) reads as 'new'. Two users can hold different statuses
    for the same shared event without interfering with each other."""
    return conn.execute(
        f"SELECT {_EVENT_COLUMNS_EXCEPT_LEGACY_STATUS}, "
        "COALESCE(ues.status, 'new') AS status "
        "FROM events e "
        "LEFT JOIN user_event_status ues ON ues.event_id = e.id AND ues.user_id = ? "
        "ORDER BY e.event_state = 'inactive', COALESCE(ues.status, 'new') = 'ignored', "
        "e.start_date IS NULL, e.start_date, e.name",
        (user_id,),
    ).fetchall()


def _load_pending_proposals(conn):
    rows = conn.execute(
        "SELECT * FROM event_change_proposals WHERE status = 'pending' "
        "ORDER BY detected_at, id"
    ).fetchall()
    by_event = {}
    for row in rows:
        by_event.setdefault(row["event_id"], []).append(row)
    return by_event


def _load_pending_recovery_candidates(conn):
    rows = conn.execute(
        "SELECT * FROM source_recovery_candidates WHERE status = 'pending' "
        "ORDER BY discovered_at, id"
    ).fetchall()
    by_event = {}
    for row in rows:
        by_event.setdefault(row["event_id"], []).append(row)
    return by_event


def _load_pending_reviews(conn):
    return conn.execute(
        "SELECT * FROM event_review_candidates WHERE status = 'pending' ORDER BY discovered_at, id"
    ).fetchall()


def _render_dashboard(error=None):
    conn = get_db()
    events = _load_dashboard_events(conn, g.user["id"])
    proposals_by_event = _load_pending_proposals(conn)
    recovery_by_event = _load_pending_recovery_candidates(conn)
    reviews = _load_pending_reviews(conn)
    conn.close()
    return render_template(
        "index.html", events=events, proposals_by_event=proposals_by_event,
        recovery_by_event=recovery_by_event, reviews=reviews, error=error,
    )


@app.route("/")
@login_required
def index():
    return _render_dashboard()


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("index"))
    next_url = request.values.get("next", "")
    if not is_safe_next_url(next_url):
        next_url = ""
    if request.method == "POST":
        conn = get_db()
        user = verify_user_password(conn, request.form.get("email", ""), request.form.get("password", ""))
        conn.close()
        if user is None:
            return render_template("login.html", error="Invalid email or password.", next=next_url), 401
        session.clear()  # drop any pre-login session state, including its CSRF token
        session["user_id"] = user["id"]
        generate_csrf_token()  # fresh token bound to the new authenticated session
        return redirect(next_url or url_for("index"))
    return render_template("login.html", error=None, next=next_url)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/event/<int:event_id>/<action>", methods=["POST"])
@login_required
def set_status(event_id, action):
    """V0.10C: writes only user_event_status for the CURRENTLY
    AUTHENTICATED user - never a bootstrap-owner fallback, never legacy
    events.status. One user's request can never touch another user's
    row (the write is always scoped to g.user["id"]). A nonexistent
    event_id is a no-op (same as the old UPDATE ... WHERE id=? matching
    zero rows)."""
    if action not in ACTION_TO_STATUS:
        abort(404)
    conn = get_db()
    try:
        set_user_event_status(conn, g.user["id"], event_id, ACTION_TO_STATUS[action])
    except ValueError:
        pass
    conn.close()
    return redirect(url_for("index"))


@app.route("/event/<int:event_id>/inactive", methods=["POST"])
@admin_required
def mark_inactive_route(event_id):
    reason = request.form.get("inactive_reason", "manual")
    if reason not in INACTIVE_REASONS:
        reason = "manual"
    conn = get_db()
    conn.execute(
        "UPDATE events SET event_state = 'inactive', inactive_reason = ?, "
        "updated_at = datetime('now') WHERE id = ?",
        (reason, event_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/event/<int:event_id>/reactivate", methods=["POST"])
@admin_required
def reactivate_route(event_id):
    conn = get_db()
    conn.execute(
        "UPDATE events SET event_state = 'active', inactive_reason = NULL, "
        "updated_at = datetime('now') WHERE id = ?",
        (event_id,),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/proposal/<int:proposal_id>/accept", methods=["POST"])
@admin_required
def accept_proposal_route(proposal_id):
    conn = get_db()
    try:
        accept_proposal(conn, proposal_id)
    except StaleProposalError as e:
        conn.close()
        return _render_dashboard(error=str(e))
    except (LookupError, ValueError) as e:
        conn.close()
        return _render_dashboard(error=f"Could not accept proposal {proposal_id}: {e}")
    conn.close()
    return redirect(url_for("index"))


@app.route("/proposal/<int:proposal_id>/dismiss", methods=["POST"])
@admin_required
def dismiss_proposal_route(proposal_id):
    conn = get_db()
    try:
        dismiss_proposal(conn, proposal_id)
    except (LookupError, ValueError) as e:
        conn.close()
        return _render_dashboard(error=f"Could not dismiss proposal {proposal_id}: {e}")
    conn.close()
    return redirect(url_for("index"))


@app.route("/source-recovery/<int:candidate_id>/accept", methods=["POST"])
@admin_required
def accept_recovery_candidate_route(candidate_id):
    conn = get_db()
    try:
        accept_recovery_candidate(conn, candidate_id)
    except (LookupError, ValueError) as e:
        conn.close()
        return _render_dashboard(error=f"Could not use candidate source {candidate_id}: {e}")
    conn.close()
    return redirect(url_for("index"))


@app.route("/source-recovery/<int:candidate_id>/dismiss", methods=["POST"])
@admin_required
def dismiss_recovery_candidate_route(candidate_id):
    conn = get_db()
    try:
        dismiss_recovery_candidate(conn, candidate_id)
    except (LookupError, ValueError) as e:
        conn.close()
        return _render_dashboard(error=f"Could not dismiss candidate source {candidate_id}: {e}")
    conn.close()
    return redirect(url_for("index"))


@app.route("/review/<int:review_id>/accept", methods=["POST"])
@admin_required
def accept_review_route(review_id):
    conn = get_db()
    try:
        accept_review_candidate(conn, review_id)
    except (LookupError, ValueError) as e:
        conn.close()
        return _render_dashboard(error=f"Could not add review candidate {review_id}: {e}")
    conn.close()
    return redirect(url_for("index"))


@app.route("/review/<int:review_id>/dismiss", methods=["POST"])
@admin_required
def dismiss_review_route(review_id):
    conn = get_db()
    try:
        dismiss_review_candidate(conn, review_id)
    except (LookupError, ValueError) as e:
        conn.close()
        return _render_dashboard(error=f"Could not dismiss review candidate {review_id}: {e}")
    conn.close()
    return redirect(url_for("index"))


@app.route("/calendar.ics")
def calendar_ics():
    """TEMPORARY LEGACY BEHAVIOR - REMOVE/REPLACE IN V0.10D.

    Deliberately unauthenticated: Apple Calendar's ICS subscription
    fetcher cannot participate in the browser login session, so for this
    transition phase this feed keeps serving the bootstrap owner's own
    Interested + active events specifically - never "whichever browser
    user happens to be logged in" (there is no logged-in user for this
    request at all). V0.10D replaces this with private per-user
    calendar_tokens URLs and removes this unauthenticated route.

    Reflects user_event_status, never the legacy events.status column. A
    plain JOIN (not LEFT JOIN) naturally excludes an event with no
    user_event_status row - missing means effective status 'new', which
    must never appear on the calendar."""
    conn = get_db()
    owner = get_or_create_bootstrap_owner(conn)
    conn.commit()  # get_or_create_bootstrap_owner doesn't commit itself
    events = conn.execute(
        "SELECT e.* FROM events e "
        "JOIN user_event_status ues ON ues.event_id = e.id AND ues.user_id = ? "
        "WHERE ues.status = 'interested' AND e.event_state = 'active' "
        "ORDER BY e.start_date",
        (owner["id"],),
    ).fetchall()
    conn.close()
    return Response(
        generate_ics(events),
        mimetype="text/calendar",
        headers={"Content-Disposition": "attachment; filename=event-radar.ics"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
