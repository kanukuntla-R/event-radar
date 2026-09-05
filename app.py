from datetime import datetime

from flask import Flask, render_template, redirect, url_for, Response, abort

from db import get_db, init_db
from calendar_service import generate_ics

app = Flask(__name__)

ACTION_TO_STATUS = {
    "interested": "interested",
    "watching": "watching",
    "ignore": "ignored",
}

init_db()


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


@app.route("/")
def index():
    conn = get_db()
    events = conn.execute(
        "SELECT * FROM events ORDER BY status = 'ignored', start_date IS NULL, start_date, name"
    ).fetchall()
    conn.close()
    return render_template("index.html", events=events)


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


@app.route("/calendar.ics")
def calendar_ics():
    conn = get_db()
    events = conn.execute(
        "SELECT * FROM events WHERE status = 'interested' ORDER BY start_date"
    ).fetchall()
    conn.close()
    return Response(
        generate_ics(events),
        mimetype="text/calendar",
        headers={"Content-Disposition": "attachment; filename=event-radar.ics"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
