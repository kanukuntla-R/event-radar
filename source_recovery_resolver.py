"""Resolves source_recovery_candidates: accept (adopt candidate_url as the
event's new source_url) or dismiss (leave the event untouched). Mirrors
proposal_resolver.py's accept/dismiss shape.
"""
from datetime import datetime, timezone


def _resolved_at(resolved_at):
    return resolved_at or datetime.now(timezone.utc).isoformat()


def _load_pending_candidate(conn, candidate_id):
    candidate = conn.execute(
        "SELECT * FROM source_recovery_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    if candidate is None:
        raise LookupError(f"No source recovery candidate with id={candidate_id!r}")
    if candidate["status"] != "pending":
        raise ValueError(
            f"Candidate {candidate_id} is already {candidate['status']!r}; cannot resolve again."
        )
    return candidate


def accept_recovery_candidate(conn, candidate_id, resolved_at=None):
    """Adopt candidate.candidate_url as the event's new source_url.

    Only events.source_url and events.updated_at change. Source-health
    history (last_checked, last_check_status, last_successful_check,
    consecutive_failures) is deliberately preserved as historical evidence -
    it is not reset just because the source changed. status, event_state,
    inactive_reason, event facts, and other proposals are untouched.

    Raises LookupError for a missing candidate/event, ValueError if the
    candidate isn't pending anymore.
    """
    candidate = _load_pending_candidate(conn, candidate_id)
    event = conn.execute(
        "SELECT id FROM events WHERE id = ?", (candidate["event_id"],)
    ).fetchone()
    if event is None:
        raise LookupError(f"No event with id={candidate['event_id']!r} for candidate {candidate_id}")

    resolved_at = _resolved_at(resolved_at)
    try:
        conn.execute(
            "UPDATE events SET source_url = ?, updated_at = ? WHERE id = ?",
            (candidate["candidate_url"], resolved_at, candidate["event_id"]),
        )
        conn.execute(
            "UPDATE source_recovery_candidates SET status = 'accepted', resolved_at = ? WHERE id = ?",
            (resolved_at, candidate_id),
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def dismiss_recovery_candidate(conn, candidate_id, resolved_at=None):
    """Mark a pending candidate dismissed. Never touches the event."""
    _load_pending_candidate(conn, candidate_id)
    resolved_at = _resolved_at(resolved_at)
    try:
        conn.execute(
            "UPDATE source_recovery_candidates SET status = 'dismissed', resolved_at = ? WHERE id = ?",
            (resolved_at, candidate_id),
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
