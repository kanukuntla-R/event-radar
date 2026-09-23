"""CLI: run one Arizona Technology Council (aztechcouncil.org) discovery
pass against the real site and real DB. Only "include"-decision events (per
asu_event_relevance.py's deterministic policy) are ever inserted; "review"
candidates are stored in the review queue (event_review_candidates) for
manual accept/dismiss from the dashboard, never inserted directly;
"exclude" is reported only. Never marks anything Interested, never touches
user_event_status.

Safe by default: --help/-h and no-argument invocation never touch the
network or the database. Exactly one of --dry-run / --persist must be given
to actually run discovery; --persist is the only mode that can write.

Usage:
    python run_az_tech_council_discovery.py --dry-run
    python run_az_tech_council_discovery.py --persist
"""
import argparse
import sys


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run one Arizona Technology Council discovery pass.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and classify candidates but write nothing to the database.",
    )
    mode.add_argument(
        "--persist", action="store_true",
        help="Run discovery and persist include/review results to the database.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    dry_run = args.dry_run

    # Imports deferred until after arg parsing so --help/-h/unknown-arg exits
    # never touch the DB or network.
    from db import get_db
    from az_tech_council_discovery_runner import run_az_tech_council_discovery

    title = "Event Radar — Arizona Technology Council Discovery" + (" (dry run)" if dry_run else "")
    print(title)
    print()

    conn = get_db()
    result = run_az_tech_council_discovery(conn, dry_run=dry_run)
    conn.close()

    new_events = [e for e in result["events"] if e["result"] == "new"]
    review_stored = [e for e in result["events"] if e["result"] == "review_stored"]
    review_existing = [e for e in result["events"] if e["result"] == "review_existing"]
    duplicates = [e for e in result["events"] if e["result"] == "duplicate"]
    invalids = [e for e in result["events"] if e["result"] == "invalid"]
    excluded = [e for e in result["events"] if e["result"] == "excluded"]
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

    print(f"EXCLUDED ({len(excluded)}):")
    for e in excluded:
        print(f"  {e['name']}: {e['relevance']['reason']}")
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
