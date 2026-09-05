"""Persists event *fact* changes detected by hackathon_change_detector as
reviewable proposals in `event_change_proposals`. Never writes to the
`events` table - proposals are accepted/dismissed by a future step, not here.
"""
from datetime import datetime, timezone

ALLOWED_FIELDS = {
    "start_date", "end_date", "date_status",
    "registration_status", "registration_url", "application_deadline",
}


def _validate_change(change):
    field = change.get("field") if isinstance(change, dict) else None
    if field not in ALLOWED_FIELDS:
        raise ValueError(f"Unsupported field for change proposals: {field!r}")
    new_value = change.get("new_value")
    if not new_value:
        raise ValueError(f"new_value must not be empty for field {field!r}, got {new_value!r}")


def _proposal_exists(conn, event_id, field, old_value, new_value):
    row = conn.execute(
        """
        SELECT 1 FROM event_change_proposals
        WHERE event_id = ? AND field = ? AND status = 'pending'
          AND old_value IS ? AND new_value = ?
        """,
        (event_id, field, old_value, new_value),
    ).fetchone()
    return row is not None


def store_change_proposals(conn, event_id, change_result, detected_at=None):
    """Store pending proposals for `change_result["changes"]` against `event_id`.

    Stores nothing if has_changes is False or changes is empty. Raises
    ValueError for an unsupported field, a missing/empty new_value, or a
    malformed change_result; raises LookupError if event_id doesn't exist.
    All changes in one call are validated before any insert, so an invalid
    entry prevents the whole batch from being written (no partial insert).
    An identical pending proposal (same event_id/field/old_value/new_value)
    is skipped rather than duplicated; accepted/dismissed proposals don't
    block a new pending one for the same change.
    """
    changes = change_result.get("changes") if isinstance(change_result, dict) else None
    if not isinstance(changes, list):
        raise ValueError("change_result['changes'] must be a list")

    if not change_result.get("has_changes") or not changes:
        return {"inserted": [], "skipped_duplicate": []}

    for change in changes:
        _validate_change(change)

    row = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise LookupError(f"No event with id={event_id!r}")

    detected_at = detected_at or datetime.now(timezone.utc).isoformat()
    source_result = change_result.get("source_result")

    inserted, skipped = [], []
    try:
        for change in changes:
            field = change["field"]
            old_value = change.get("old_value")
            new_value = change["new_value"]
            entry = {"field": field, "old_value": old_value, "new_value": new_value}
            if _proposal_exists(conn, event_id, field, old_value, new_value):
                skipped.append(entry)
                continue
            conn.execute(
                """
                INSERT INTO event_change_proposals
                    (event_id, field, old_value, new_value, evidence,
                     source_result, detected_at, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (event_id, field, old_value, new_value, change.get("evidence"),
                 source_result, detected_at, detected_at),
            )
            inserted.append(entry)
    except Exception:
        conn.rollback()
        raise

    conn.commit()
    return {"inserted": inserted, "skipped_duplicate": skipped}
