"""CLI: run one ASU Career Services discovery pass against the real site and
real DB. Discovers new career-focused events into Event Radar with
status='new' - it never marks anything Interested.

Invoked by event-radar-asu-career.timer/.service (deploy/systemd/) with NO
arguments, every 6 hours - so, unlike run_az_tech_council_discovery.py,
no-argument invocation deliberately still runs a real persistent pass
(dry_run=False). --help/-h and unknown arguments never touch the DB or
network, same safety guarantee as that other CLI - only the no-args default
differs, to preserve the live timer's existing contract.

Usage:
    python run_asu_career_discovery.py              # persistent run (dry_run=False)
    python run_asu_career_discovery.py --dry-run
"""
import argparse
import sys


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run one ASU Career Services discovery pass. With no arguments, "
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
    from asu_career_discovery_runner import run_asu_career_discovery

    title = "Event Radar — ASU Career Services Discovery" + (" (dry run)" if dry_run else "")
    print(title)
    print()

    conn = get_db()
    result = run_asu_career_discovery(conn, dry_run=dry_run)
    conn.close()

    for event in result["events"]:
        if event["result"] == "new":
            print(f"NEW: {event['name']}")
        elif event["result"] == "duplicate":
            print(f"DUPLICATE: {event['name']} (existing event id {event['existing_event_id']}, {event['reason']})")
        elif event["result"] == "invalid":
            print(f"INVALID: {event['name']} ({event['reason']})")
        elif event["result"] == "runner_error":
            print(f"ERROR: {event['name']} ({event['error']})")
    print()

    s = result["summary"]
    print("Summary")
    print(f"Pages fetched: {s['pages_fetched']}")
    print(f"Candidates found: {s['candidates_found']}")
    print(f"Inserted: {s['inserted']}")
    print(f"Duplicates skipped: {s['duplicates_skipped']}")
    print(f"Invalid skipped: {s['invalid_skipped']}")
    print(f"Runner errors: {s['runner_errors']}")


if __name__ == "__main__":
    main()
