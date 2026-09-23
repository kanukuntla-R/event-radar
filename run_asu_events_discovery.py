"""CLI: run one ASU Events (asuevents.asu.edu) discovery pass against the
real site and real DB. Only "include"-decision events (per
event_relevance.py's deterministic policy) are ever inserted; "review"
candidates are stored in the review queue (event_review_candidates) for
manual accept/dismiss from the dashboard, never inserted directly;
"exclude" is reported only. Never marks anything Interested.

Invoked by event-radar-asu-events.timer/.service (deploy/systemd/) with NO
arguments, every 6 hours - so, unlike run_az_tech_council_discovery.py,
no-argument invocation deliberately still runs a real persistent pass
(dry_run=False). --help/-h and unknown arguments never touch the DB or
network, same safety guarantee as that other CLI - only the no-args default
differs, to preserve the live timer's existing contract.

Usage:
    python run_asu_events_discovery.py              # persistent run (dry_run=False)
    python run_asu_events_discovery.py --dry-run
"""
import argparse
import sys


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run one ASU Events discovery pass. With no arguments, "
                     "runs a real persistent pass (matches the live systemd timer's contract).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and classify candidates but write nothing to the database.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    dry_run = args.dry_run

    # Imports deferred until after arg parsing so --help/-h/unknown-arg exits
    # never touch the DB or network.
    from db import get_db
    from asu_events_discovery_runner import run_asu_events_discovery

    title = "Event Radar — ASU Events Discovery" + (" (dry run)" if dry_run else "")
    print(title)
    print()

    conn = get_db()
    result = run_asu_events_discovery(conn, dry_run=dry_run)
    conn.close()

    new_events = [e for e in result["events"] if e["result"] == "new"]
    review_stored = [e for e in result["events"] if e["result"] == "review_stored"]
    review_existing = [e for e in result["events"] if e["result"] == "review_existing"]
    duplicates = [e for e in result["events"] if e["result"] == "duplicate"]
    invalids = [e for e in result["events"] if e["result"] == "invalid"]
    errors = [e for e in result["events"] if e["result"] == "runner_error"]

    print(f"INCLUDE ({len(new_events)}):")
    for e in new_events:
        print(f"  {e['name']} (event id {e['event_id']})")
    print()

    print(f"REVIEW STORED ({len(review_stored)}):")
    for e in review_stored:
        print(f"  {e['name']} (review id {e['review_id']})")
    print()

    print(f"REVIEW EXISTING ({len(review_existing)}):")
    for e in review_existing:
        print(f"  {e['name']} -> review id {e['review_id']} (status: {e['status']}, reason: {e['reason']})")
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
    print(f"Review stored: {s['review_stored']}")
    print(f"Review existing: {s['review_existing']}")
    print(f"Invalid skipped: {s['invalid_skipped']}")
    print(f"Runner errors: {s['runner_errors']}")


if __name__ == "__main__":
    main()
