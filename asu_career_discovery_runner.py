"""Orchestrates one ASU Career Services discovery pass: collect the
listing, normalize each hit, and store new ones. Composes the existing
modules - never reimplements their logic, never marks a discovered event
Interested.
"""
from asu_career_collector import collect_listing
from asu_event_normalizer import normalize_candidate
from event_discovery_store import store_discovered_events


def run_asu_career_discovery(conn, collect_func=None, discovered_at=None, dry_run=False, **collect_kwargs):
    """Run one ASU Career Services discovery pass.

    `collect_func` defaults to asu_career_collector.collect_listing (real
    stdlib HTTP); inject a fake for offline tests. Every raw listing hit is
    normalized (asu_event_normalizer.normalize_candidate) and the whole
    batch is handed to event_discovery_store.store_discovered_events, which
    validates, deduplicates, and (unless dry_run) inserts. dry_run performs
    the full collection/normalization/dedup pass with zero DB writes - it
    differs from the source-recovery/hackathon-runner dry-runs in that it
    still fetches and parses the site, since its purpose is to preview what
    would be added.

    A normalization crash for one raw hit is caught and reported as a
    runner_error without stopping the batch.

    Returns {"summary": {...}, "events": [...]}.
    """
    collect_func = collect_func or collect_listing
    collection = collect_func(**collect_kwargs)
    raw_candidates = collection["candidates"]
    pages_fetched = collection["pages_fetched"]

    normalized = []
    runner_errors = []
    for raw in raw_candidates:
        try:
            normalized.append(normalize_candidate(raw))
        except Exception as e:
            try:
                name = raw.get("name")
            except Exception:
                name = None
            runner_errors.append({"name": name, "error": f"{type(e).__name__}: {e}"})

    outcome = store_discovered_events(conn, normalized, discovered_at=discovered_at, dry_run=dry_run)

    events = (
        [{"name": e["name"], "result": "new", "event_id": e["event_id"]} for e in outcome["inserted"]]
        + [
            {"name": d["name"], "result": "duplicate", "existing_event_id": d["existing_event_id"], "reason": d["reason"]}
            for d in outcome["duplicates_skipped"]
        ]
        + [{"name": i["name"], "result": "invalid", "reason": i["reason"]} for i in outcome["invalid_skipped"]]
        + [{"name": r["name"], "result": "runner_error", "error": r["error"]} for r in runner_errors]
    )

    summary = {
        "pages_fetched": pages_fetched,
        "candidates_found": len(raw_candidates),
        "inserted": len(outcome["inserted"]),
        "duplicates_skipped": len(outcome["duplicates_skipped"]),
        "invalid_skipped": len(outcome["invalid_skipped"]),
        "runner_errors": len(runner_errors),
    }
    return {"summary": summary, "events": events}
