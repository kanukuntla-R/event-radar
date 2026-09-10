import json
import os
from datetime import datetime

from flask import Flask, render_template, redirect, url_for, Response, abort, request, session, g, make_response

from db import get_db, init_db
from calendar_service import generate_ics
from proposal_resolver import accept_proposal, dismiss_proposal, StaleProposalError
from source_health import classify_source_health
from source_recovery_resolver import accept_recovery_candidate, dismiss_recovery_candidate
from event_review_resolver import accept_review_candidate, dismiss_review_candidate
from user_store import (
    set_user_event_status, delete_user_event_status, verify_user_password,
    get_active_calendar_token_for_user, create_calendar_token,
    rotate_calendar_token, revoke_calendar_token, get_user_for_calendar_token,
)
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

# V0.11.1: "new" is a distinct action from ACTION_TO_STATUS's set/update
# actions - it deletes the user_event_status row rather than upserting an
# explicit status='new' row. New is represented ONLY by a missing row (see
# delete_user_event_status in user_store.py).
REVERT_TO_NEW_ACTIONS = {"new"}

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

# V0.11.1: display labels for the event-card type badge. Same
# dict-driven-lookup-with-fallback pattern as CANDIDATE_TYPE_LABELS above -
# an unlisted event_type just falls back to a generic Title Case rendering
# (event_type_label below), so this never needs a source-specific code
# path for a new event_type value to display reasonably.
EVENT_TYPE_LABELS = {
    "asu": "ASU",
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


@app.template_filter("event_type_label")
def event_type_label(event_type):
    if not event_type:
        return event_type
    return EVENT_TYPE_LABELS.get(event_type, event_type.replace("_", " ").title())


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


DASHBOARD_FILTER_KEYS = ("q", "status", "source", "type", "location_type", "sort")

_SORT_OPTIONS = {
    "upcoming": "e.event_state = 'inactive', effective_status = 'ignored', "
                "e.start_date IS NULL, e.start_date, e.name",
    "recent": "e.first_seen DESC, e.id DESC",
}


def _load_dashboard_events(conn, user_id, q=None, status=None, source=None,
                            event_type=None, location_type=None, sort="upcoming"):
    """V0.10C: `status` comes from user_event_status.status for the
    CURRENTLY AUTHENTICATED user - never the bootstrap owner, never the
    legacy events.status column. A missing row (an event this user hasn't
    acted on yet) reads as 'new'. Two users can hold different statuses
    for the same shared event without interfering with each other.

    V0.11: q/status/source/event_type/location_type narrow this same
    per-user set further - search and every filter here can only narrow,
    never widen, what this query would otherwise return."""
    where = []
    params = [user_id]
    if status and status != "all":
        where.append("COALESCE(ues.status, 'new') = ?")
        params.append(status)
    if source:
        where.append("e.source = ?")
        params.append(source)
    if event_type:
        where.append("e.event_type = ?")
        params.append(event_type)
    if location_type:
        where.append("e.location_type = ?")
        params.append(location_type)
    if q:
        needle = f"%{q.strip().lower()}%"
        where.append(
            "(lower(e.name) LIKE ? OR lower(COALESCE(e.description, '')) LIKE ? "
            "OR lower(COALESCE(e.location, '')) LIKE ? OR lower(COALESCE(e.source, '')) LIKE ?)"
        )
        params.extend([needle, needle, needle, needle])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = _SORT_OPTIONS.get(sort, _SORT_OPTIONS["upcoming"])
    return conn.execute(
        f"SELECT {_EVENT_COLUMNS_EXCEPT_LEGACY_STATUS}, "
        "COALESCE(ues.status, 'new') AS status, "
        "COALESCE(ues.status, 'new') AS effective_status "
        "FROM events e "
        "LEFT JOIN user_event_status ues ON ues.event_id = e.id AND ues.user_id = ? "
        f"{where_sql} "
        f"ORDER BY {order_sql}",
        params,
    ).fetchall()


def _dashboard_summary_counts(conn, user_id):
    """Per-user New/Interested/Watching/Ignored counts over the same
    unfiltered per-user event set _load_dashboard_events(...) returns
    with no filters - stays stable as the user narrows the view below."""
    rows = conn.execute(
        "SELECT COALESCE(ues.status, 'new') AS status, COUNT(*) AS n "
        "FROM events e "
        "LEFT JOIN user_event_status ues ON ues.event_id = e.id AND ues.user_id = ? "
        "GROUP BY COALESCE(ues.status, 'new')",
        (user_id,),
    ).fetchall()
    counts = {"new": 0, "interested": 0, "watching": 0, "ignored": 0}
    for row in rows:
        counts[row["status"]] = row["n"]
    return counts


def _dashboard_filter_choices(conn):
    """Source/type/location-type dropdown options, populated from
    whatever values currently exist in events - never hardcoded."""
    def _distinct(col):
        return sorted(
            r[0] for r in conn.execute(
                f"SELECT DISTINCT {col} FROM events WHERE {col} IS NOT NULL AND {col} != ''"
            ).fetchall()
        )
    return {
        "sources": _distinct("source"),
        "event_types": _distinct("event_type"),
        "location_types": _distinct("location_type"),
    }


def _dashboard_args_from(mapping):
    """Whitelist-only extraction of dashboard filter/search/sort state from
    a Flask MultiDict (request.args on GET, request.form on the
    status-change POST). Never passes anything else through, so this can
    never become an open-redirect vector regardless of what a client
    sends - the caller only ever feeds the result into url_for('index',
    **result), which always resolves to this app's own dashboard path."""
    result = {}
    for key in DASHBOARD_FILTER_KEYS:
        value = (mapping.get(key) or "").strip()
        if value:
            result[key] = value
    return result


def _dashboard_status_url(filters, status):
    """V0.11.1 UI polish: builds a same-origin dashboard URL that preserves
    every current filter (q/source/type/location_type/sort) except status,
    which is replaced - or removed entirely for the "All" tile (status
    falsy). Powers the clickable summary tiles. Like
    _dashboard_args_from, this only ever resolves via url_for('index',
    ...), so it can never become an open redirect regardless of what's in
    `filters`."""
    merged = dict(filters)
    if status:
        merged["status"] = status
    else:
        merged.pop("status", None)
    return url_for("index", **merged)


app.jinja_env.globals["dashboard_status_url"] = _dashboard_status_url


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
    filters = _dashboard_args_from(request.args)
    events = _load_dashboard_events(
        conn, g.user["id"], q=filters.get("q"), status=filters.get("status"),
        source=filters.get("source"), event_type=filters.get("type"),
        location_type=filters.get("location_type"), sort=filters.get("sort", "upcoming"),
    )
    summary = _dashboard_summary_counts(conn, g.user["id"])
    choices = _dashboard_filter_choices(conn)
    proposals_by_event = _load_pending_proposals(conn)
    recovery_by_event = _load_pending_recovery_candidates(conn)
    reviews = _load_pending_reviews(conn)
    conn.close()
    return render_template(
        "index.html", events=events, proposals_by_event=proposals_by_event,
        recovery_by_event=recovery_by_event, reviews=reviews, error=error,
        summary=summary, choices=choices, filters=filters,
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
    zero rows).

    V0.11: redirects back to whatever filtered/search dashboard view the
    user was on, rebuilt entirely from a fixed whitelist of hidden form
    fields (DASHBOARD_FILTER_KEYS) via url_for - never from a raw
    client-supplied URL, so this can never become an open redirect.

    V0.11.1: the "new" action is a DELETE of this user's row, scoped by
    BOTH g.user["id"] and event_id - never an upsert of status='new'. New
    is represented only by a missing row (see delete_user_event_status);
    every dashboard read already treats a missing row as 'new' via
    COALESCE(ues.status, 'new'), so this needs no other code to change."""
    if action not in ACTION_TO_STATUS and action not in REVERT_TO_NEW_ACTIONS:
        abort(404)
    conn = get_db()
    if action in REVERT_TO_NEW_ACTIONS:
        delete_user_event_status(conn, g.user["id"], event_id)
    else:
        try:
            set_user_event_status(conn, g.user["id"], event_id, ACTION_TO_STATUS[action])
        except ValueError:
            pass
    conn.close()
    return redirect(url_for("index", **_dashboard_args_from(request.form)))


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


def _external_base_url():
    """Base URL (scheme+host[:port], no trailing slash) for building a
    private calendar subscription URL. Prefers EVENT_RADAR_EXTERNAL_BASE_URL
    (for a future public/Cloudflare deployment behind a different
    hostname) and otherwise falls back to the current request's own Host
    header. Deliberately does NOT trust X-Forwarded-* headers (no
    ProxyFix) - this deployment has no reverse proxy in front of Gunicorn
    yet, and blindly trusting forwarded headers from arbitrary clients
    would let a request spoof its own scheme/host."""
    configured = os.environ.get("EVENT_RADAR_EXTERNAL_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return request.host_url.rstrip("/")


def _calendar_subscription_url(raw_token):
    return _external_base_url() + url_for("private_calendar_ics", token=raw_token)


@app.route("/calendar")
@login_required
def calendar_settings():
    conn = get_db()
    active_token = get_active_calendar_token_for_user(conn, g.user["id"])
    conn.close()
    return render_template("calendar_settings.html", active_token=active_token, new_token_url=None, error=None)


def _token_reveal_response(**template_args):
    """create/rotate responses carry a one-time-viewable raw calendar token
    URL - more sensitive than the ordinary settings page, so they always
    get their own no-store/no-referrer headers regardless of what the
    global response defaults are."""
    resp = make_response(render_template("calendar_settings.html", **template_args))
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.route("/calendar/token/create", methods=["POST"])
@login_required
def create_calendar_token_route():
    conn = get_db()
    try:
        raw_token = create_calendar_token(conn, g.user["id"])
    except ValueError:
        active_token = get_active_calendar_token_for_user(conn, g.user["id"])
        conn.close()
        return _token_reveal_response(
            active_token=active_token, new_token_url=None,
            error="A calendar subscription is already active.",
        )
    active_token = get_active_calendar_token_for_user(conn, g.user["id"])
    conn.close()
    return _token_reveal_response(
        active_token=active_token,
        new_token_url=_calendar_subscription_url(raw_token), rotated=False, error=None,
    )


@app.route("/calendar/token/rotate", methods=["POST"])
@login_required
def rotate_calendar_token_route():
    conn = get_db()
    raw_token = rotate_calendar_token(conn, g.user["id"])
    active_token = get_active_calendar_token_for_user(conn, g.user["id"])
    conn.close()
    return _token_reveal_response(
        active_token=active_token,
        new_token_url=_calendar_subscription_url(raw_token), rotated=True, error=None,
    )


@app.route("/calendar/token/revoke", methods=["POST"])
@login_required
def revoke_calendar_token_route():
    conn = get_db()
    revoke_calendar_token(conn, g.user["id"])
    conn.close()
    return redirect(url_for("calendar_settings"))


@app.route("/calendar/<token>.ics")
def private_calendar_ics(token):
    """V0.10D private per-user ICS feed, authenticated exclusively by the
    bearer token in the URL - NOT by the browser session (g.user is never
    read here). A logged-in browser session, or no session at all, must
    never change whose feed is returned - only the token does. Any
    failure (nonexistent, revoked, or malformed token) is the same
    generic 404 - never distinguishing why, and never redirecting to
    login."""
    conn = get_db()
    user = get_user_for_calendar_token(conn, token)
    if user is None:
        conn.close()
        abort(404)
    events = conn.execute(
        "SELECT e.* FROM events e "
        "JOIN user_event_status ues ON ues.event_id = e.id AND ues.user_id = ? "
        "WHERE ues.status = 'interested' AND e.event_state = 'active' "
        "ORDER BY e.start_date",
        (user["id"],),
    ).fetchall()
    conn.close()
    return Response(
        generate_ics(events),
        mimetype="text/calendar",
        headers={
            "Content-Disposition": "attachment; filename=event-radar.ics",
            # Private per-user feed - never cache/store on a shared/intermediate cache.
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
        },
    )



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
