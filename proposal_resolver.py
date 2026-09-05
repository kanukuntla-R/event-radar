"""Resolves event_change_proposals: accept (apply the single field to the
event) or dismiss (leave the event untouched). Both just flip proposal
status - nothing here decides *what* to propose, that's proposal_store.py.
"""
from datetime import datetime, timezone

ALLOWED_FIELDS = {
    "start_date", "end_date", "date_status",
    "registration_status", "registration_url", "application_deadline",
}


class StaleProposalError(Exception):
    """The event's current field value no longer matches proposal.old_value -
    something else changed it since this proposal was detected."""


def _normalize(value):
    return None if value in (None, "") else value


def _resolved_at(resolved_at):
    return resolved_at or datetime.now(timezone.utc).isoformat()


def _load_pending_proposal(conn, proposal_id):
    proposal = conn.execute(
        "SELECT * FROM event_change_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if proposal is None:
        raise LookupError(f"No proposal with id={proposal_id!r}")
    if proposal["field"] not in ALLOWED_FIELDS:
        raise ValueError(f"Unsupported field on proposal {proposal_id}: {proposal['field']!r}")
    if proposal["status"] != "pending":
        raise ValueError(
            f"Proposal {proposal_id} is already {proposal['status']!r}; cannot resolve again."
        )
    return proposal


def accept_proposal(conn, proposal_id, resolved_at=None):
    """Apply proposal.new_value to the event's single target field, then mark
    the proposal accepted. Refuses (StaleProposalError) if the event's
    current value for that field no longer matches proposal.old_value - the
    event and proposal are left completely unchanged in that case. Raises
    LookupError for a missing proposal/event, ValueError for a bad field or a
    proposal that isn't pending anymore.
    """
    proposal = _load_pending_proposal(conn, proposal_id)
    event = conn.execute(
        "SELECT * FROM events WHERE id = ?", (proposal["event_id"],)
    ).fetchone()
    if event is None:
        raise LookupError(f"No event with id={proposal['event_id']!r} for proposal {proposal_id}")

    field = proposal["field"]
    current_value = _normalize(event[field])
    expected_old_value = _normalize(proposal["old_value"])
    if current_value != expected_old_value:
        raise StaleProposalError(
            f"Proposal {proposal_id} expected {field}={expected_old_value!r} but the "
            f"event currently has {field}={current_value!r}; leaving both unchanged."
        )

    resolved_at = _resolved_at(resolved_at)
    try:
        conn.execute(
            f"UPDATE events SET {field} = ?, updated_at = ? WHERE id = ?",
            (proposal["new_value"], resolved_at, proposal["event_id"]),
        )
        conn.execute(
            "UPDATE event_change_proposals SET status = 'accepted', resolved_at = ? WHERE id = ?",
            (resolved_at, proposal_id),
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def dismiss_proposal(conn, proposal_id, resolved_at=None):
    """Mark a pending proposal dismissed. Never touches the event."""
    _load_pending_proposal(conn, proposal_id)
    resolved_at = _resolved_at(resolved_at)
    try:
        conn.execute(
            "UPDATE event_change_proposals SET status = 'dismissed', resolved_at = ? WHERE id = ?",
            (resolved_at, proposal_id),
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
