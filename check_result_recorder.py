"""Records ONLY source-check health/bookkeeping from a CollectorResult into
SQLite. Never touches event facts (dates, registration, etc.), `status`, or
lifecycle decisions (`event_state`, `inactive_reason`) - this module counts
failures, it does not decide what to do about them.
"""
from datetime import datetime

SUCCESS_RESULTS = {"ok", "redirected"}
FAILURE_RESULTS = {"not_found", "unavailable", "parse_failed"}


def _validate_checked_at(checked_at):
    if not checked_at or not isinstance(checked_at, str):
        raise ValueError(f"checked_at must be a non-empty ISO datetime string, got {checked_at!r}")
    try:
        datetime.fromisoformat(checked_at)
    except ValueError as e:
        raise ValueError(f"checked_at is not a valid ISO datetime string: {checked_at!r}") from e
    return checked_at


def record_check_result(conn, event_id, collector_result):
    """Update source-check health fields for `event_id` from `collector_result`.

    Writes exactly one of two field sets, in a single atomic UPDATE:
      success (ok/redirected): last_checked, last_check_status,
                                last_successful_check, consecutive_failures=0
      failure (not_found/unavailable/parse_failed): last_checked,
                                last_check_status, consecutive_failures += 1
                                (last_successful_check left untouched)

    Raises ValueError for an unrecognized source_result or a missing/invalid
    checked_at, and LookupError if event_id doesn't exist - in every error
    case, no DB write happens at all. Never sets event_state or
    inactive_reason; the 3+-failures review threshold is policy for a later
    step, not something this function decides.
    """
    source_result = collector_result.get("source_result")
    if source_result not in SUCCESS_RESULTS and source_result not in FAILURE_RESULTS:
        raise ValueError(f"Unrecognized source_result: {source_result!r}")

    checked_at = _validate_checked_at(collector_result.get("checked_at"))

    row = conn.execute(
        "SELECT consecutive_failures FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"No event with id={event_id!r}")

    if source_result in SUCCESS_RESULTS:
        conn.execute(
            """
            UPDATE events
            SET last_checked = ?,
                last_check_status = ?,
                last_successful_check = ?,
                consecutive_failures = 0
            WHERE id = ?
            """,
            (checked_at, source_result, checked_at, event_id),
        )
    else:
        previous_failures = row["consecutive_failures"] if hasattr(row, "keys") else row[0]
        conn.execute(
            """
            UPDATE events
            SET last_checked = ?,
                last_check_status = ?,
                consecutive_failures = ?
            WHERE id = ?
            """,
            (checked_at, source_result, previous_failures + 1, event_id),
        )

    conn.commit()
