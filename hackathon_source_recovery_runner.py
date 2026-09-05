"""Orchestrates one batch pass of source-recovery discovery over eligible
hackathons: run the recovery collector, rank/classify hits, and store them
as pending candidates. Composes the existing modules - never reimplements
their logic, never writes events.source_url itself.
"""
from source_recovery import rank_candidate
from hackathon_source_recovery_collector import discover_candidates
from source_recovery_store import store_recovery_candidates

ELIGIBILITY_FAILURE_THRESHOLD = 3


def _eligible_events(conn):
    return conn.execute(
        "SELECT * FROM events WHERE event_type = 'hackathon' AND event_state = 'active' "
        "AND consecutive_failures >= ?",
        (ELIGIBILITY_FAILURE_THRESHOLD,),
    ).fetchall()


def _empty_entry(event):
    return {
        "event_id": event["id"],
        "name": event["name"],
        "result": None,
        "candidates_found": 0,
        "candidates_inserted": 0,
        "duplicates_skipped": 0,
        "error": None,
    }


def _evaluate_only(event):
    entry = _empty_entry(event)
    entry["result"] = "eligible"
    return entry


def _process_event(conn, event, now, discover_func):
    entry = _empty_entry(event)
    try:
        raw_candidates = discover_func(event, now=now)
        ranked = [rank_candidate(event, c) for c in raw_candidates]
        entry["candidates_found"] = len(ranked)

        if ranked:
            outcome = store_recovery_candidates(conn, event["id"], ranked)
            entry["candidates_inserted"] = len(outcome["inserted"])
            entry["duplicates_skipped"] = len(outcome["skipped_duplicate"])

        entry["result"] = "processed"
    except Exception as e:
        conn.rollback()
        entry["result"] = "runner_error"
        entry["error"] = f"{type(e).__name__}: {e}"
    return entry


def run_source_recovery(conn, now=None, discover_func=None, dry_run=False):
    """Process every active hackathon with consecutive_failures >= 3.

    For each: hackathon_source_recovery_collector.discover_candidates() (or
    an injected `discover_func`) finds raw hits, source_recovery.rank_candidate
    classifies/scores them, and source_recovery_store.store_recovery_candidates
    persists them as pending rows (duplicates skipped, never re-inserted).
    Nothing here ever writes events.source_url.

    dry_run=True only reports which events are eligible - no discovery call,
    no writes at all.

    An unexpected exception for one event is caught, the connection is
    rolled back, and that event is reported "runner_error" with a readable
    `error` string; the batch continues with the remaining events.

    Returns {"events": [...], "summary": {...}}.
    """
    discover_func = discover_func or discover_candidates
    events = _eligible_events(conn)

    if dry_run:
        results = [_evaluate_only(event) for event in events]
    else:
        results = [_process_event(conn, event, now, discover_func) for event in events]

    summary = {
        "eligible": len(results),
        "processed": sum(1 for r in results if r["result"] == "processed"),
        "candidates_found": sum(r["candidates_found"] for r in results),
        "candidates_inserted": sum(r["candidates_inserted"] for r in results),
        "duplicates_skipped": sum(r["duplicates_skipped"] for r in results),
        "runner_errors": sum(1 for r in results if r["result"] == "runner_error"),
    }
    return {"events": results, "summary": summary}
