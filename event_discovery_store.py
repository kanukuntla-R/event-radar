"""Validates and stores newly discovered events (ASU Career Services, and
future discovery sources) into the existing events table. Never modifies an
existing row - duplicates are skipped, not merged or updated. Never marks a
discovered event Interested: everything lands as status='new'.
"""
from datetime import datetime, timezone

from event_dedup import find_duplicate, match_reason

REQUIRED_FIELDS = ("name", "source_url", "start_date")

INSERT_SQL = """
INSERT INTO events (
    name, event_type, description, start_date, end_date, start_time, end_time,
    timezone, date_status, location, location_type, location_area,
    event_subtype, source, source_url, organizer, registration_status,
    registration_url, application_deadline, status, event_state, first_seen
) VALUES (
    :name, :event_type, :description, :start_date, :end_date, :start_time, :end_time,
    :timezone, :date_status, :location, :location_type, :location_area,
    :event_subtype, :source, :source_url, :organizer, :registration_status,
    :registration_url, :application_deadline, 'new', 'active', :first_seen
)
"""

DEFAULTS = {
    "event_type": "asu", "description": None, "end_date": None,
    "start_time": None, "end_time": None, "timezone": None,
    "location": None, "location_type": None, "location_area": None,
    "event_subtype": None, "source": "ASU Career Services", "organizer": "ASU Career Services",
    "registration_status": "unknown", "registration_url": None, "application_deadline": None,
}


def validate_candidate(candidate):
    """Pure check: does `candidate` carry the minimum facts to be storable?

    Returns (is_valid, reason). Never invents missing data - a candidate
    lacking a name, source_url, or start_date is invalid rather than guessed
    at, since none of those can be safely defaulted.

    A start_time of None is only acceptable when the candidate explicitly
    marks itself `all_day=True` (source text actually said "All Day"). A
    timeless candidate that isn't confirmed all-day is rejected here rather
    than stored - a stored event with start_time=None is treated as all-day
    everywhere downstream (dashboard, ICS), so silently accepting it would
    turn a merely-unparsed time into a fabricated all-day calendar entry.
    """
    for field in REQUIRED_FIELDS:
        if not candidate.get(field):
            return False, f"missing required field: {field}"
    if candidate.get("start_time") is None and not candidate.get("all_day", False):
        return False, "time evidence missing or unparseable, and not confirmed all-day"
    return True, None


def store_discovered_events(conn, candidates, discovered_at=None, dry_run=False):
    """Validate, deduplicate, and (unless dry_run) insert `candidates`
    (normalized dicts from asu_event_normalizer.normalize_candidate).

    For each candidate: an invalid one (validate_candidate) is skipped and
    reported; a duplicate (event_dedup.find_duplicate against the DB, plus
    an in-memory check against candidates already accepted earlier in this
    same call, so repeats across paginated listing pages are still caught)
    is skipped and reported with the existing event id/reason; everything
    else is genuinely new and inserted with status='new', event_state='active'.

    dry_run=True runs every check (including the DB-backed duplicate
    lookup) but issues no INSERT/commit - the only difference from a real
    run is that nothing is written. Never touches an existing row.

    Returns {"inserted": [...], "duplicates_skipped": [...],
    "invalid_skipped": [...], "event_ids": [...]}.
    """
    discovered_at = discovered_at or datetime.now(timezone.utc).isoformat()
    inserted, duplicates, invalid = [], [], []
    accepted_this_batch = []

    for candidate in candidates:
        is_valid, reason = validate_candidate(candidate)
        if not is_valid:
            invalid.append({"name": candidate.get("name"), "reason": reason})
            continue

        dup = find_duplicate(conn, candidate)
        if not dup["is_duplicate"]:
            for prior in accepted_this_batch:
                batch_reason = match_reason(candidate, prior)
                if batch_reason:
                    dup = {"is_duplicate": True, "existing_event_id": None, "reason": batch_reason}
                    break

        if dup["is_duplicate"]:
            duplicates.append({
                "name": candidate.get("name"), "existing_event_id": dup["existing_event_id"],
                "reason": dup["reason"],
            })
            continue

        accepted_this_batch.append(candidate)

        if dry_run:
            inserted.append({"name": candidate["name"], "event_id": None})
            continue

        row = dict(DEFAULTS)
        row.update(candidate)
        row["first_seen"] = discovered_at
        cur = conn.execute(INSERT_SQL, row)
        inserted.append({"name": candidate["name"], "event_id": cur.lastrowid})

    if not dry_run:
        conn.commit()

    return {
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "invalid_skipped": invalid,
        "event_ids": [e["event_id"] for e in inserted if e["event_id"] is not None],
    }
