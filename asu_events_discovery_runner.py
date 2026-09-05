"""Orchestrates one ASU Events discovery pass: collect the listing,
normalize, apply the deterministic relevance filter, and store only
`include` candidates. `review`/`exclude` candidates are reported but never
stored - see asu_event_relevance.py for the (non-ML) decision policy.
Reuses event_discovery_store.store_discovered_events() unmodified for the
actual insertion, same as the ASU Career Services pipeline.
"""
from asu_events_collector import collect_listing
from asu_events_normalizer import normalize_candidate
from asu_event_relevance import classify_relevance
from event_discovery_store import store_discovered_events


def run_asu_events_discovery(conn, collect_func=None, discovered_at=None, dry_run=False, **collect_kwargs):
    """Run one ASU Events discovery pass.

    `collect_func` defaults to asu_events_collector.collect_listing (real
    stdlib HTTP); inject a fake for offline tests. Every raw listing hit is
    normalized, then classified by asu_event_relevance.classify_relevance():
    only "include" candidates are handed to
    event_discovery_store.store_discovered_events (validated, deduplicated,
    and - unless dry_run - inserted). "review" and "exclude" candidates are
    reported but never reach the store. dry_run performs the full
    collection/normalization/relevance/dedup pass with zero DB writes.

    A crash while normalizing/classifying one raw hit is caught and
    reported as a runner_error without stopping the batch.

    Returns {"summary": {...}, "events": [...]}.
    """
    collect_func = collect_func or collect_listing
    collection = collect_func(**collect_kwargs)
    raw_candidates = collection["candidates"]
    pages_fetched = collection["pages_fetched"]

    to_store = []
    reviewed, excluded, runner_errors = [], [], []

    for raw in raw_candidates:
        try:
            candidate = normalize_candidate(raw)
            relevance = classify_relevance(candidate)
            if relevance["decision"] == "include":
                to_store.append(candidate)
            elif relevance["decision"] == "review":
                reviewed.append({"candidate": candidate, "relevance": relevance})
            else:
                excluded.append({"name": candidate.get("name"), "relevance": relevance})
        except Exception as e:
            try:
                name = raw.get("name")
            except Exception:
                name = None
            runner_errors.append({"name": name, "error": f"{type(e).__name__}: {e}"})

    outcome = store_discovered_events(conn, to_store, discovered_at=discovered_at, dry_run=dry_run)

    events = (
        [{"name": e["name"], "result": "new", "event_id": e["event_id"]} for e in outcome["inserted"]]
        + [
            {
                "name": d["name"], "result": "duplicate", "existing_event_id": d["existing_event_id"],
                "existing_source": d.get("existing_source"), "candidate_source": d.get("candidate_source"),
                "reason": d["reason"],
            }
            for d in outcome["duplicates_skipped"]
        ]
        + [{"name": i["name"], "result": "invalid", "reason": i["reason"]} for i in outcome["invalid_skipped"]]
        + [
            {
                "name": r["candidate"].get("name"), "result": "review",
                "candidate": r["candidate"], "relevance": r["relevance"],
            }
            for r in reviewed
        ]
        + [{"name": e["name"], "result": "excluded", "relevance": e["relevance"]} for e in excluded]
        + [{"name": r["name"], "result": "runner_error", "error": r["error"]} for r in runner_errors]
    )

    summary = {
        "pages_fetched": pages_fetched,
        "candidates_found": len(raw_candidates),
        "included": len(to_store),
        "review": len(reviewed),
        "excluded": len(excluded),
        "inserted": len(outcome["inserted"]),
        "duplicates_skipped": len(outcome["duplicates_skipped"]),
        "invalid_skipped": len(outcome["invalid_skipped"]),
        "runner_errors": len(runner_errors),
    }
    return {"summary": summary, "events": events}
