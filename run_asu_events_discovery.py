"""CLI: run one ASU Events (asuevents.asu.edu) discovery pass against the
real site and real DB. Only "include"-decision events (per
asu_event_relevance.py's deterministic policy) are ever inserted; "review"
and "exclude" are reported only. Never marks anything Interested.

Usage:
    python run_asu_events_discovery.py
    python run_asu_events_discovery.py --dry-run
"""
import sys

from db import get_db
from asu_events_discovery_runner import run_asu_events_discovery


def _fmt_candidate(candidate):
    date = candidate.get("start_date") or "?"
    time_part = f" {candidate['start_time']}" if candidate.get("start_time") else ""
    location = candidate.get("location") or "?"
    subtype = candidate.get("event_subtype") or "?"
    return f"  {date}{time_part} | {location} | {subtype}"


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    title = "Event Radar — ASU Events Discovery" + (" (dry run)" if dry_run else "")
    print(title)
    print()

    conn = get_db()
    result = run_asu_events_discovery(conn, dry_run=dry_run)
    conn.close()

    new_events = [e for e in result["events"] if e["result"] == "new"]
    reviews = [e for e in result["events"] if e["result"] == "review"]
    duplicates = [e for e in result["events"] if e["result"] == "duplicate"]
    invalids = [e for e in result["events"] if e["result"] == "invalid"]
    errors = [e for e in result["events"] if e["result"] == "runner_error"]

    print(f"INCLUDE ({len(new_events)}):")
    for e in new_events:
        print(f"  {e['name']} (event id {e['event_id']})")
    print()

    print(f"REVIEW ({len(reviews)}):")
    for e in reviews:
        c = e["candidate"]
        print(f"{e['name']}")
        print(_fmt_candidate(c))
        print(f"  reason: {e['relevance']['reason']} | signals: {', '.join(e['relevance']['signals']) or '(none)'}")
        print(f"  source: {c.get('source_url')}")
    print()

    print(f"DUPLICATE ({len(duplicates)}):")
    for e in duplicates:
        print(f"  {e['name']} -> existing event id {e['existing_event_id']} "
              f"(existing source: {e['existing_source']}, candidate source: {e['candidate_source']}, reason: {e['reason']})")
    print()

    print(f"INVALID ({len(invalids)}):")
    for e in invalids:
        print(f"  {e['name']}: {e['reason']}")
    if errors:
        print(f"\nRUNNER ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e['name']}: {e['error']}")
    print()

    s = result["summary"]
    print("Summary")
    print(f"Pages fetched: {s['pages_fetched']}")
    print(f"Candidates found: {s['candidates_found']}")
    print(f"Included: {s['included']}")
    print(f"Review: {s['review']}")
    print(f"Excluded: {s['excluded']}")
    print(f"Inserted: {s['inserted']}")
    print(f"Duplicates skipped: {s['duplicates_skipped']}")
    print(f"Invalid skipped: {s['invalid_skipped']}")
    print(f"Runner errors: {s['runner_errors']}")


if __name__ == "__main__":
    main()
