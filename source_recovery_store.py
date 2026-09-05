"""Persists ranked source-recovery candidates (from source_recovery.py) as
reviewable pending rows in SQLite. Never touches events.source_url - that's
an explicit source_recovery_resolver action. Mirrors proposal_store.py's
validate-then-insert, duplicate-prevention pattern.
"""
from datetime import datetime, timezone

ALLOWED_TYPES = {
    "official_event", "official_organizer", "official_university",
    "mlh", "official_social", "other",
}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def _validate_candidate(candidate):
    if not candidate.get("candidate_url"):
        raise ValueError(f"candidate_url must not be empty, got {candidate.get('candidate_url')!r}")
    if candidate.get("candidate_type") not in ALLOWED_TYPES:
        raise ValueError(f"Unsupported candidate_type: {candidate.get('candidate_type')!r}")
    if candidate.get("confidence") not in ALLOWED_CONFIDENCE:
        raise ValueError(f"Unsupported confidence: {candidate.get('confidence')!r}")


def _candidate_exists(conn, event_id, candidate_url):
    row = conn.execute(
        "SELECT 1 FROM source_recovery_candidates "
        "WHERE event_id = ? AND candidate_url = ? AND status = 'pending'",
        (event_id, candidate_url),
    ).fetchone()
    return row is not None


def store_recovery_candidates(conn, event_id, candidates, discovered_at=None):
    """Store `candidates` (each shaped like source_recovery.rank_candidate's
    output) as pending source_recovery_candidates rows for `event_id`.

    Raises ValueError for a missing candidate_url or an unsupported
    candidate_type/confidence, and LookupError if event_id doesn't exist.
    All candidates are validated before any insert, so one invalid entry
    prevents the whole batch from being written. An identical pending
    candidate (same event_id + candidate_url) is skipped, not duplicated; an
    accepted/dismissed candidate doesn't block a fresh pending one for the
    same URL. Never writes to events. Returns {"inserted": [...],
    "skipped_duplicate": [...]}.
    """
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    if not candidates:
        return {"inserted": [], "skipped_duplicate": []}

    for candidate in candidates:
        _validate_candidate(candidate)

    row = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise LookupError(f"No event with id={event_id!r}")

    discovered_at = discovered_at or datetime.now(timezone.utc).isoformat()

    inserted, skipped = [], []
    try:
        for candidate in candidates:
            url = candidate["candidate_url"]
            entry = {"candidate_url": url, "candidate_type": candidate["candidate_type"]}
            if _candidate_exists(conn, event_id, url):
                skipped.append(entry)
                continue
            conn.execute(
                """
                INSERT INTO source_recovery_candidates
                    (event_id, candidate_url, candidate_type, title, evidence,
                     confidence, discovered_at, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (event_id, url, candidate["candidate_type"], candidate.get("title"),
                 candidate.get("evidence"), candidate["confidence"], discovered_at, discovered_at),
            )
            inserted.append(entry)
    except Exception:
        conn.rollback()
        raise

    conn.commit()
    return {"inserted": inserted, "skipped_duplicate": skipped}
