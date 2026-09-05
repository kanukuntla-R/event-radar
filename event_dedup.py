"""Deterministic duplicate-event detection for discovery pipelines (ASU
Career Services and future sources). Read-only: only SELECTs, never writes
or merges an existing row. Strong evidence (same source_url) wins first;
name+date+compatible-location is a fallback, never bare name similarity.
"""
from source_recovery import normalize_url


def _get(obj, key, default=None):
    try:
        value = obj[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def match_reason(candidate, existing):
    """Pure comparison: why (if at all) does `candidate` duplicate
    `existing`? `existing` may be a sqlite3.Row or a plain dict. Returns a
    reason string ("same_source_url" / "same_name_date_location") or None.
    """
    candidate_url = normalize_url(candidate.get("source_url"))
    existing_url = normalize_url(_get(existing, "source_url"))
    if candidate_url and existing_url and candidate_url == existing_url:
        return "same_source_url"

    name = (candidate.get("name") or "").strip().lower()
    existing_name = (_get(existing, "name") or "").strip().lower()
    start_date = candidate.get("start_date")
    existing_start = _get(existing, "start_date")

    if name and start_date and name == existing_name and start_date == existing_start:
        location = (candidate.get("location") or "").strip().lower()
        existing_location = (_get(existing, "location") or "").strip().lower()
        # Compatible = at least one side has no location evidence, or they
        # agree - never merge same-name/date events at clearly different
        # locations.
        if not location or not existing_location or location == existing_location:
            return "same_name_date_location"

    return None


def find_duplicate(conn, candidate):
    """Scan existing events for one matching `candidate`. Returns
    {"is_duplicate": bool, "existing_event_id": int|None, "reason": str|None}.
    Never modifies the database.
    """
    rows = conn.execute("SELECT id, name, source_url, start_date, location FROM events")
    for row in rows:
        reason = match_reason(candidate, row)
        if reason:
            return {"is_duplicate": True, "existing_event_id": row["id"], "reason": reason}
    return {"is_duplicate": False, "existing_event_id": None, "reason": None}
