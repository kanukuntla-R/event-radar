"""Orchestrates one batch pass over active hackathons: ask the strategy if a
check is due, run the collector, record source health, detect fact changes,
and store any changes as pending proposals. Composes the existing modules -
never reimplements their logic, never applies a proposal, never touches an
event's facts/status/lifecycle fields itself.
"""
from hackathon_strategy import evaluate
from hackathon_collector import collect
from hackathon_change_detector import detect_changes
from check_result_recorder import record_check_result
from proposal_store import store_change_proposals

SOURCE_SUCCESS_RESULTS = {"ok", "redirected"}
SOURCE_FAILURE_RESULTS = {"not_found", "unavailable", "parse_failed"}


def _empty_entry(event):
    return {
        "event_id": event["id"],
        "name": event["name"],
        "result": None,
        "strategy_reason": None,
        "next_check": None,
        "source_result": None,
        "proposals_inserted": 0,
        "proposals_skipped_duplicate": 0,
        "error": None,
    }


def _evaluate_only(event, today):
    """Dry-run path: strategy only, no collector call, no DB write."""
    entry = _empty_entry(event)
    try:
        strategy_result = evaluate(event, today=today)
        entry["strategy_reason"] = strategy_result["reason"]
        entry["next_check"] = strategy_result["next_check"]
        entry["result"] = "would_check" if strategy_result["should_check"] else "skipped"
    except Exception as e:
        entry["result"] = "runner_error"
        entry["error"] = f"{type(e).__name__}: {e}"
    return entry


def _process_event(conn, event, today, now, collector_func):
    entry = _empty_entry(event)
    try:
        strategy_result = evaluate(event, today=today)
        entry["strategy_reason"] = strategy_result["reason"]
        entry["next_check"] = strategy_result["next_check"]

        if not strategy_result["should_check"]:
            entry["result"] = "skipped"
            return entry

        collector_result = collector_func(event, now=now)
        entry["source_result"] = collector_result["source_result"]

        record_check_result(conn, event["id"], collector_result)

        change_result = detect_changes(event, collector_result)
        if change_result["has_changes"]:
            outcome = store_change_proposals(
                conn, event["id"], change_result, detected_at=collector_result["checked_at"]
            )
            entry["proposals_inserted"] = len(outcome["inserted"])
            entry["proposals_skipped_duplicate"] = len(outcome["skipped_duplicate"])

        entry["result"] = "checked"
    except Exception as e:
        conn.rollback()
        entry["result"] = "runner_error"
        entry["error"] = f"{type(e).__name__}: {e}"
    return entry


def _summarize(results):
    skipped = sum(1 for r in results if r["result"] == "skipped")
    checked = sum(1 for r in results if r["result"] == "checked")
    would_check = sum(1 for r in results if r["result"] == "would_check")
    runner_errors = sum(1 for r in results if r["result"] == "runner_error")

    summary = {
        "total_active_hackathons": len(results),
        "due": len(results) - skipped,
        "checked": checked,
        "skipped": skipped,
        "source_success": sum(1 for r in results if r["source_result"] in SOURCE_SUCCESS_RESULTS),
        "source_failure": sum(1 for r in results if r["source_result"] in SOURCE_FAILURE_RESULTS),
        "runner_errors": runner_errors,
        "proposals_inserted": sum(r["proposals_inserted"] for r in results),
        "proposals_skipped_duplicate": sum(r["proposals_skipped_duplicate"] for r in results),
    }
    if would_check:
        summary["would_check"] = would_check
    return summary


def run_due_hackathon_checks(conn, today=None, now=None, collector_func=None, dry_run=False):
    """Process every active hackathon due for a check.

    Only `event_type = 'hackathon'` and `event_state = 'active'` rows are
    considered. For each: hackathon_strategy.evaluate() decides should_check;
    if not due, it's reported "skipped" and the collector is never called. If
    due (and dry_run is False), the collector runs, check_result_recorder
    records source health, hackathon_change_detector compares facts, and any
    detected changes are stored via proposal_store (duplicates are skipped,
    never re-inserted). Nothing here writes an event fact field, changes
    status/event_state/inactive_reason, or applies a proposal.

    dry_run=True only runs the strategy step - no collector call, no writes
    at all - and reports "would_check"/"skipped" instead of "checked".

    An unexpected exception for one event is caught, the connection is
    rolled back, and that event is reported "runner_error" with a readable
    `error` string; the batch continues with the remaining events. A
    source-level failure (not_found/unavailable/parse_failed) is a normal,
    already-handled CollectorResult, not a runner_error.

    Returns {"events": [...], "summary": {...}}.
    """
    collector_func = collector_func or collect
    events = conn.execute(
        "SELECT * FROM events WHERE event_type = 'hackathon' AND event_state = 'active'"
    ).fetchall()

    if dry_run:
        results = [_evaluate_only(event, today) for event in events]
    else:
        results = [_process_event(conn, event, today, now, collector_func) for event in events]

    return {"events": results, "summary": _summarize(results)}
