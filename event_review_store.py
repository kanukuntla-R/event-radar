"""Stores ASU Events discovery candidates the relevance filter marked
"review" into a dedicated review queue - never the events table. Dedup
reuses event_dedup.match_reason's pure comparison (name+date+compatible
time/location, or identical source_url) applied against
event_review_candidates rows instead of events, so it never mutates events.

Policy is asymmetric by row status:

- A matching PENDING row (via match_reason - source_url OR name/date/
  compatible time+location) always suppresses a new one: don't queue a
  second row while one is already waiting for a decision.
- An ACCEPTED/DISMISSED row is different: a human already decided on that
  *exact event snapshot*, not on "anything that ever appears at that URL".
  Same source_url alone is NOT enough to suppress forever - the same ASU
  Events URL can later host a materially different event (new date/time/
  location). A resolved row only suppresses a new candidate when their
  material snapshots (name/start_date/end_date/start_time/end_time/
  location, normalized) are exactly the same; otherwise a fresh pending row
  is created. No versioning system - just a direct snapshot comparison.
"""
import json
import re
from datetime import datetime, timezone

from event_dedup import match_reason

MATERIAL_FIELDS = ("start_date", "end_date", "start_time", "end_time", "location")

REQUIRED_FIELDS = ("name", "source_url", "source")

INSERT_SQL = """
INSERT INTO event_review_candidates (
    name, event_type, event_subtype, description, start_date, end_date,
    start_time, end_time, timezone, location, location_type, location_area,
    source, source_url, organizer, registration_status, registration_url,
    application_deadline, all_day, review_reason, review_signals, discovered_at,
    status, created_at
) VALUES (
    :name, :event_type, :event_subtype, :description, :start_date, :end_date,
    :start_time, :end_time, :timezone, :location, :location_type, :location_area,
    :source, :source_url, :organizer, :registration_status, :registration_url,
    :application_deadline, :all_day, :review_reason, :review_signals, :discovered_at,
    'pending', :discovered_at
)
"""

DEFAULTS = {
    "event_type": "asu", "event_subtype": None, "description": None,
    "start_date": None, "end_date": None, "start_time": None, "end_time": None,
    "timezone": None, "location": None, "location_type": None, "location_area": None,
    "organizer": None, "registration_status": "unknown", "registration_url": None,
    "application_deadline": None, "all_day": 0, "review_reason": None, "review_signals": None,
}


def validate_review_candidate(candidate):
    """Pure check: minimum facts to queue for review, plus the same
    timed-event safety invariant as event_discovery_store.validate_candidate
    - but only when a start_date is actually present (a dateless candidate
    can't become a false all-day calendar entry, so it's fine to queue for
    review as genuinely incomplete). Returns (is_valid, reason).
    """
    for field in REQUIRED_FIELDS:
        if not candidate.get(field):
            return False, f"missing required field: {field}"
    if candidate.get("start_date") and candidate.get("start_time") is None and not candidate.get("all_day", False):
        return False, "time evidence missing or unparseable, and not confirmed all-day"
    return True, None


def _get(obj, key, default=None):
    try:
        value = obj[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _normalize_name(name):
    text = (name or "").strip().lower().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_field(value):
    return (value or "").strip().lower()


def _material_snapshot(obj):
    """(name, start_date, end_date, start_time, end_time, location), all
    normalized. Deliberately excludes description - trivial wording edits
    there shouldn't matter for identity.
    """
    return (_normalize_name(_get(obj, "name")),) + tuple(
        _normalize_field(_get(obj, field)) for field in MATERIAL_FIELDS
    )


def find_existing_review(conn, candidate):
    """Look for an event_review_candidates row that should suppress a new
    pending row for `candidate`. Returns {"exists": bool,
    "review_id": int|None, "status": str|None, "reason": str|None}. Never
    modifies the database.

    A pending row matches via event_dedup.match_reason (source_url, or the
    usual name/date/location fallback) - any match suppresses, preserving
    existing idempotence. A resolved (accepted/dismissed) row only matches
    when its material snapshot (see _material_snapshot) is identical -
    same source_url alone is not enough, since the same URL can later host
    a materially different event.
    """
    rows = conn.execute(
        "SELECT id, name, source_url, start_date, end_date, start_time, end_time, "
        "location, source, status FROM event_review_candidates"
    ).fetchall()
    for row in rows:
        if row["status"] == "pending":
            reason = match_reason(candidate, row)
            if reason:
                return {"exists": True, "review_id": row["id"], "status": row["status"], "reason": reason}
        elif _material_snapshot(candidate) == _material_snapshot(row):
            return {"exists": True, "review_id": row["id"], "status": row["status"], "reason": "same_material_snapshot"}
    return {"exists": False, "review_id": None, "status": None, "reason": None}


def store_review_candidates(conn, candidates, discovered_at=None, dry_run=False):
    """Validate and (unless dry_run) store `candidates` as pending review
    rows. Each candidate should carry `review_reason`/`review_signals`
    (from asu_event_relevance.classify_relevance) in addition to the usual
    normalized discovery fields.

    An invalid candidate is skipped and reported; one matching an existing
    review row (any status) is skipped and reported as already-seen rather
    than re-queued. Never writes to events. dry_run runs every check
    (including the DB-backed existing-review lookup) with zero writes.

    Returns {"stored": [...], "existing": [...], "invalid_skipped": [...]}.
    """
    discovered_at = discovered_at or datetime.now(timezone.utc).isoformat()
    stored, existing, invalid = [], [], []
    accepted_this_batch = []

    for candidate in candidates:
        is_valid, reason = validate_review_candidate(candidate)
        if not is_valid:
            invalid.append({"name": candidate.get("name"), "reason": reason})
            continue

        dup = find_existing_review(conn, candidate)
        if not dup["exists"]:
            for prior in accepted_this_batch:
                batch_reason = match_reason(candidate, prior)
                if batch_reason:
                    dup = {"exists": True, "review_id": None, "status": "pending", "reason": batch_reason}
                    break

        if dup["exists"]:
            existing.append({
                "name": candidate.get("name"), "review_id": dup["review_id"],
                "status": dup["status"], "reason": dup["reason"],
            })
            continue

        accepted_this_batch.append(candidate)

        if dry_run:
            stored.append({"name": candidate.get("name"), "review_id": None})
            continue

        row = dict(DEFAULTS)
        row.update(candidate)
        row["discovered_at"] = discovered_at
        row["all_day"] = 1 if candidate.get("all_day") else 0
        row["review_reason"] = candidate.get("review_reason")
        row["review_signals"] = json.dumps(candidate.get("review_signals") or [])
        cur = conn.execute(INSERT_SQL, row)
        stored.append({"name": candidate.get("name"), "review_id": cur.lastrowid})

    if not dry_run:
        conn.commit()

    return {"stored": stored, "existing": existing, "invalid_skipped": invalid}
