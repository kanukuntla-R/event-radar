"""Pure change-detection layer: compare a stored event record against a
hackathon_collector CollectorResult and propose fact changes.

No SQLite access, no HTTP, no mutation of either input, no database writes.
Lifecycle fields (event_state, inactive_reason, consecutive_failures,
last_checked, last_successful_check, last_check_status) are never touched
here - that belongs to a later step, same as source_url management for a
redirected final_url.
"""

COMPARED_FIELDS = (
    "start_date",
    "end_date",
    "date_status",
    "registration_status",
    "registration_url",
    "application_deadline",
)

# Which raw_evidence key backs a proposed change for each field. Never
# invented - if the key isn't present in raw_evidence, evidence is None.
FIELD_EVIDENCE_KEYS = {
    "start_date": "date_match_text",
    "end_date": "date_match_text",
    "date_status": "date_match_text",
    "registration_status": "registration_phrase",
    "registration_url": "registration_link_text",
    "application_deadline": "deadline_match_text",
}

# Fact comparison only proceeds when the source was actually reachable and
# readable. Any other source_result (not_found, unavailable, parse_failed,
# or anything unrecognized) skips comparison entirely.
COMPARABLE_SOURCE_RESULTS = {"ok", "redirected"}


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _normalize(value):
    """None and "" both mean "no meaningful value" - never a real replacement."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def detect_changes(event, collector_result):
    """Compare `event`'s stored facts against `collector_result`.

    Neither `event` nor `collector_result` is mutated. Returns a dict with
    has_changes, source_result, changes, unchanged_fields, ignored_fields,
    notes - see module docstring for scope.
    """
    source_result = collector_result.get("source_result")

    if source_result not in COMPARABLE_SOURCE_RESULTS:
        return {
            "has_changes": False,
            "source_result": source_result,
            "changes": [],
            "unchanged_fields": [],
            "ignored_fields": list(COMPARED_FIELDS),
            "notes": [f"Source check returned '{source_result}'; fact comparison skipped."],
        }

    detected = collector_result.get("detected") or {}
    raw_evidence = collector_result.get("raw_evidence") or {}

    changes = []
    unchanged_fields = []
    ignored_fields = []

    for field in COMPARED_FIELDS:
        old_value = _normalize(_get(event, field))
        new_value = _normalize(detected.get(field))

        if new_value is None:
            ignored_fields.append(field)  # rule 1: no signal -> never overwrite
            continue
        if new_value == old_value:
            unchanged_fields.append(field)
            continue

        changes.append({
            "field": field,
            "old_value": old_value,
            "new_value": new_value,
            "evidence": raw_evidence.get(FIELD_EVIDENCE_KEYS[field]),
        })

    notes = (
        [f"{len(changes)} field(s) differ from the stored event."] if changes
        else ["No differences found between stored event and collector result."]
    )

    return {
        "has_changes": bool(changes),
        "source_result": source_result,
        "changes": changes,
        "unchanged_fields": unchanged_fields,
        "ignored_fields": ignored_fields,
        "notes": notes,
    }
