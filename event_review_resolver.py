"""Resolves event_review_candidates: accept (validate/dedup/insert into
events using the exact same rules as normal discovery) or dismiss (leave
events untouched). Mirrors proposal_resolver.py/source_recovery_resolver.py's
accept/dismiss shape.
"""
from datetime import datetime, timezone

from event_dedup import find_duplicate
from event_discovery_store import validate_candidate, INSERT_SQL, DEFAULTS


def _resolved_at(resolved_at):
    return resolved_at or datetime.now(timezone.utc).isoformat()


def _load_pending_review(conn, review_id):
    row = conn.execute("SELECT * FROM event_review_candidates WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise LookupError(f"No review candidate with id={review_id!r}")
    if row["status"] != "pending":
        raise ValueError(f"Review candidate {review_id} is already {row['status']!r}; cannot resolve again.")
    return row


def _candidate_from_review_row(row):
    return {
        "name": row["name"],
        "description": row["description"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "timezone": row["timezone"],
        "date_status": "confirmed" if row["start_date"] else None,
        "location": row["location"],
        "location_type": row["location_type"],
        "location_area": row["location_area"],
        # V0.12A: was missing entirely - every review candidate silently
        # reverted to event_discovery_store.DEFAULTS['event_type'] ('asu')
        # on accept, regardless of what event_type was actually stored on
        # the review row. Invisible while every review-path source was ASU
        # (same value either way); a real bug for any other source's
        # candidates (e.g. Arizona Technology Council's 'professional').
        "event_type": row["event_type"],
        "event_subtype": row["event_subtype"],
        "source": row["source"],
        "source_url": row["source_url"],
        "organizer": row["organizer"],
        "registration_status": row["registration_status"],
        "registration_url": row["registration_url"],
        "application_deadline": row["application_deadline"],
        # V0.9B.2: use the persisted all_day column directly - do NOT infer
        # it from start_time being NULL. That inference happened to be
        # validation-safe today (a stored row with a start_date and no
        # start_time currently can only exist because
        # event_review_store.validate_review_candidate already required
        # all_day=True to store it) but silently discarded the real
        # evidence and was fragile to that one gate never changing.
        "all_day": bool(row["all_day"]),
    }


def accept_review_candidate(conn, review_id, resolved_at=None):
    """Validate and (if not a duplicate) insert a pending review candidate
    into events, using the exact same validation (event_discovery_store.
    validate_candidate) and dedup (event_dedup.find_duplicate) rules as
    normal discovery.

    Raises LookupError for a missing candidate, ValueError if it isn't
    pending anymore or fails validation (nothing is written in that case).
    If it duplicates an existing event, no second event is created - the
    review candidate is still marked accepted (the real event already
    exists), and `existing_event_id` identifies it. Otherwise inserts with
    status='new', event_state='active' - never Interested - then marks the
    review candidate accepted.

    Returns {"event_id": int|None, "duplicate": bool, "existing_event_id": int|None}.
    """
    row = _load_pending_review(conn, review_id)
    candidate = _candidate_from_review_row(row)

    is_valid, reason = validate_candidate(candidate)
    if not is_valid:
        raise ValueError(f"Review candidate {review_id} is not a valid event: {reason}")

    resolved_at = _resolved_at(resolved_at)
    dup = find_duplicate(conn, candidate)

    try:
        event_id = None
        if not dup["is_duplicate"]:
            insert_row = dict(DEFAULTS)
            insert_row.update(candidate)
            insert_row["first_seen"] = resolved_at
            cur = conn.execute(INSERT_SQL, insert_row)
            event_id = cur.lastrowid

        conn.execute(
            "UPDATE event_review_candidates SET status = 'accepted', resolved_at = ? WHERE id = ?",
            (resolved_at, review_id),
        )
    except Exception:
        conn.rollback()
        raise

    conn.commit()
    return {"event_id": event_id, "duplicate": dup["is_duplicate"], "existing_event_id": dup["existing_event_id"]}


def dismiss_review_candidate(conn, review_id, resolved_at=None):
    """Mark a pending review candidate dismissed. Never touches events."""
    _load_pending_review(conn, review_id)
    resolved_at = _resolved_at(resolved_at)
    try:
        conn.execute(
            "UPDATE event_review_candidates SET status = 'dismissed', resolved_at = ? WHERE id = ?",
            (resolved_at, review_id),
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
