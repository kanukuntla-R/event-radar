"""Orchestrates one Arizona Technology Council discovery pass: collect the
listing, normalize, apply the deterministic relevance filter, and route by
decision:

  include -> event_discovery_store (validated, deduplicated, inserted)
  review  -> event_review_store (validated, deduplicated against the review
             queue - never the events table; held for human accept/dismiss)
  exclude -> reported only, never persisted anywhere

Reuses asu_event_relevance.classify_relevance - despite the module name,
its policy (STRONG_INCLUDE_KEYWORDS/TECHNICAL_INCLUDE_KEYWORDS/
STRONG_EXCLUDE_KEYWORDS) is pure name/description keyword matching with no
ASU-specific logic, and already covers exactly the interest categories this
source cares about (career/professional, software/tech, cybersecurity,
cloud, AI/data, engineering, startups/innovation). Its category-based
checks (STRONG_EXCLUDE_CATEGORIES/SUPPORTING_CATEGORIES) reference ASU's
own taxonomy strings and simply never match AZ Tech Council's different
category slugs - a harmless no-op, not a source-specific branch added here.
Mirrors asu_events_discovery_runner.py's shape exactly.
"""
from az_tech_council_collector import collect_listing
from az_tech_council_normalizer import normalize_candidate
from asu_event_relevance import classify_relevance
from event_discovery_store import store_discovered_events
from event_review_store import store_review_candidates


def run_az_tech_council_discovery(conn, collect_func=None, discovered_at=None, dry_run=False, **collect_kwargs):
    """Run one Arizona Technology Council discovery pass.

    `collect_func` defaults to az_tech_council_collector.collect_listing
    (real stdlib HTTP); inject a fake for offline tests. Every raw listing
    hit is normalized, then classified by
    asu_event_relevance.classify_relevance(). "include" candidates go to
    event_discovery_store.store_discovered_events; "review" candidates go
    to event_review_store.store_review_candidates (never the events table);
    "exclude" candidates are reported only. dry_run performs the full
    collection/normalization/relevance/dedup pass with zero DB writes to
    either table.

    A crash while normalizing/classifying one raw hit is caught and
    reported as a runner_error without stopping the batch.

    Returns {"summary": {...}, "events": [...]}.
    """
    collect_func = collect_func or collect_listing
    collection = collect_func(**collect_kwargs)
    raw_candidates = collection["candidates"]
    pages_fetched = collection["pages_fetched"]

    to_store, to_review = [], []
    excluded, runner_errors = [], []

    for raw in raw_candidates:
        try:
            candidate = normalize_candidate(raw)
            relevance = classify_relevance(candidate)
            if relevance["decision"] == "include":
                to_store.append(candidate)
            elif relevance["decision"] == "review":
                candidate["review_reason"] = relevance["reason"]
                candidate["review_signals"] = relevance["signals"]
                to_review.append(candidate)
            else:
                excluded.append({"name": candidate.get("name"), "relevance": relevance})
        except Exception as e:
            try:
                name = raw.get("name")
            except Exception:
                name = None
            runner_errors.append({"name": name, "error": f"{type(e).__name__}: {e}"})

    outcome = store_discovered_events(conn, to_store, discovered_at=discovered_at, dry_run=dry_run)
    review_outcome = store_review_candidates(conn, to_review, discovered_at=discovered_at, dry_run=dry_run)

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
        + [{"name": s["name"], "result": "review_stored", "review_id": s["review_id"]} for s in review_outcome["stored"]]
        + [
            {
                "name": e["name"], "result": "review_existing", "review_id": e["review_id"],
                "status": e["status"], "reason": e["reason"],
            }
            for e in review_outcome["existing"]
        ]
        + [{"name": i["name"], "result": "invalid", "reason": i["reason"]} for i in review_outcome["invalid_skipped"]]
        + [{"name": e["name"], "result": "excluded", "relevance": e["relevance"]} for e in excluded]
        + [{"name": r["name"], "result": "runner_error", "error": r["error"]} for r in runner_errors]
    )

    summary = {
        "pages_fetched": pages_fetched,
        "candidates_found": len(raw_candidates),
        "included": len(to_store),
        "review": len(to_review),
        "excluded": len(excluded),
        "inserted": len(outcome["inserted"]),
        "duplicates_skipped": len(outcome["duplicates_skipped"]),
        "review_stored": len(review_outcome["stored"]),
        "review_existing": len(review_outcome["existing"]),
        "invalid_skipped": len(outcome["invalid_skipped"]) + len(review_outcome["invalid_skipped"]),
        "runner_errors": len(runner_errors),
    }
    return {"summary": summary, "events": events}
