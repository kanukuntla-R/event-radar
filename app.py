from datetime import datetime

from flask import Flask, render_template, redirect, url_for, Response, abort, request

from db import get_db, init_db
from calendar_service import generate_ics
from proposal_resolver import accept_proposal, dismiss_proposal, StaleProposalError
from source_health import classify_source_health
from source_recovery_resolver import accept_recovery_candidate, dismiss_recovery_candidate

app = Flask(__name__)

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


def _fmt_date(d):
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _fmt_time(t):
    return t.strftime("%I:%M %p").lstrip("0")


@app.template_filter("event_date")
def format_event_date(e):
    if e["date_status"] == "tba" or not e["start_date"]:
        return "TBA"

    start_date = datetime.strptime(e["start_date"], "%Y-%m-%d")
    date_label = _fmt_date(start_date)

    if e["start_time"]:
        start_time = datetime.strptime(e["start_time"], "%H:%M")
        label = f"{date_label} • {_fmt_time(start_time)}"
        if e["end_time"]:
            end_time = datetime.strptime(e["end_time"], "%H:%M")
            label += f" – {_fmt_time(end_time)}"
        return label

    if e["end_date"] and e["end_date"] != e["start_date"]:
        end_date = datetime.strptime(e["end_date"], "%Y-%m-%d")
        return f"{date_label} – {_fmt_date(end_date)}"

    return date_label


def _load_dashboard_events(conn):
    return conn.execute(
        "SELECT * FROM events "
        "ORDER BY event_state = 'inactive', status = 'ignored', start_date IS NULL, start_date, name"
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


def _render_dashboard(error=None):
    conn = get_db()
    events = _load_dashboard_events(conn)
    proposals_by_event = _load_pending_proposals(conn)
    recovery_by_event = _load_pending_recovery_candidates(conn)
    conn.close()
    return render_template(
        "index.html", events=events, proposals_by_event=proposals_by_event,
        recovery_by_event=recovery_by_event, error=error,
    )


@app.route("/")
def index():
    return _render_dashboard()


@app.route("/event/<int:event_id>/<action>", methods=["POST"])
def set_status(event_id, action):
    if action not in ACTION_TO_STATUS:
        abort(404)
    conn = get_db()
    conn.execute(
        "UPDATE events SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (ACTION_TO_STATUS[action], event_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/event/<int:event_id>/inactive", methods=["POST"])
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
def dismiss_recovery_candidate_route(candidate_id):
    conn = get_db()
    try:
        dismiss_recovery_candidate(conn, candidate_id)
    except (LookupError, ValueError) as e:
        conn.close()
        return _render_dashboard(error=f"Could not dismiss candidate source {candidate_id}: {e}")
    conn.close()
    return redirect(url_for("index"))


@app.route("/calendar.ics")
def calendar_ics():
    conn = get_db()
    events = conn.execute(
        "SELECT * FROM events WHERE status = 'interested' AND event_state = 'active' "
        "ORDER BY start_date"
    ).fetchall()
    conn.close()
    return Response(
        generate_ics(events),
        mimetype="text/calendar",
        headers={"Content-Disposition": "attachment; filename=event-radar.ics"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
