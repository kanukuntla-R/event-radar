"""CLI: run one ASU Career Services discovery pass against the real site and
real DB. Discovers new career-focused events into Event Radar with
status='new' - it never marks anything Interested.

Usage:
    python run_asu_career_discovery.py
    python run_asu_career_discovery.py --dry-run
"""
import sys

from db import get_db
from asu_career_discovery_runner import run_asu_career_discovery


def main():
    dry_run = "--dry-run" in sys.argv[1:]
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
