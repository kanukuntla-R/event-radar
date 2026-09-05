"""Deterministic duplicate-event detection for discovery pipelines (ASU
Career Services, ASU Events, and future sources). Read-only: only SELECTs,
never writes or merges an existing row. Strong evidence (same source_url)
wins first; name+date+compatible-time+compatible-location is a fallback,
never bare name similarity - and the fallback deliberately never checks
`source`, so it catches the same real-world event listed under two
different sources (e.g. "ASU Career Services" and "ASU Events") with two
different URLs.
"""
import re

from event_location import classify_location, PRIORITY_BY_AREA
from source_recovery import normalize_url

# Areas meaningful enough to prove two locations are the same place.
# "other" means "unmapped/unknown", not "a specific shared location" - two
# different unmapped buildings must never match just because neither was
# recognized.
MEANINGFUL_AREAS = set(PRIORITY_BY_AREA) - {"other"}


def _get(obj, key, default=None):
    try:
        value = obj[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _normalize_name_for_match(name):
    """Lowercase, "&"->"and", punctuation stripped, whitespace collapsed -
    just enough to catch "Fall Career & Internship Fair" vs "Fall Career
    and Internship Fair" without fuzzy-matching genuinely different titles.
    """
    text = (name or "").strip().lower().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_reason(candidate, existing):
    """Pure comparison: why (if at all) does `candidate` duplicate
    `existing`? `existing` may be a sqlite3.Row or a plain dict. Returns a
    reason string ("same_source_url" / "same_name_date_location") or None.
    """
    candidate_url = normalize_url(candidate.get("source_url"))
    existing_url = normalize_url(_get(existing, "source_url"))
    if candidate_url and existing_url and candidate_url == existing_url:
        return "same_source_url"

    name = _normalize_name_for_match(candidate.get("name"))
    existing_name = _normalize_name_for_match(_get(existing, "name"))
    start_date = candidate.get("start_date")
    existing_start = _get(existing, "start_date")

    if name and start_date and name == existing_name and start_date == existing_start:
        location = (candidate.get("location") or "").strip().lower()
        existing_location = (_get(existing, "location") or "").strip().lower()
        # Raw text rarely matches word-for-word across sources ("Tempe,
        # ECG" vs "Tempe campus"), so also compare derived location_area
        # ("tempe" == "tempe") - but only for a *meaningful* area. Both
        # sides mapping to "other" means neither was recognized, not that
        # they're the same unknown place ("Wexler Hall" vs "Biodesign
        # Auditorium" must not match just because both are area=other).
        candidate_area = classify_location(candidate.get("location"))["location_area"]
        existing_area = classify_location(_get(existing, "location"))["location_area"]
        meaningful_area_match = (
            candidate_area and existing_area
            and candidate_area == existing_area
            and candidate_area in MEANINGFUL_AREAS
        )
        # Compatible = at least one side has no evidence, the raw text
        # agrees exactly, or they share a meaningful area - never merge
        # same-name/date events at clearly different (or both-unknown)
        # locations/times.
        location_compatible = (
            not location or not existing_location
            or location == existing_location
            or meaningful_area_match
        )

        start_time = candidate.get("start_time")
        existing_time = _get(existing, "start_time")
        time_compatible = not start_time or not existing_time or start_time == existing_time

        if location_compatible and time_compatible:
            return "same_name_date_location"

    return None


def find_duplicate(conn, candidate):
    """Scan existing events for one matching `candidate`. Returns
    {"is_duplicate": bool, "existing_event_id": int|None, "reason": str|None}
    - plus "existing_source" (the matched row's `source` column) when a
    duplicate is found, for cross-source reporting. Never modifies the
    database.
    """
    rows = conn.execute("SELECT id, name, source_url, start_date, start_time, location, source FROM events")
    for row in rows:
        reason = match_reason(candidate, row)
        if reason:
            return {
                "is_duplicate": True, "existing_event_id": row["id"],
                "reason": reason, "existing_source": row["source"],
            }
    return {"is_duplicate": False, "existing_event_id": None, "reason": None}
