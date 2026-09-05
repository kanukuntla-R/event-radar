"""CLI: run one batch pass of source-recovery discovery against the real DB
and real collector. Only considers active hackathons with 3+ consecutive
source failures. Never writes events.source_url - discovered candidates are
stored as pending rows for review in the dashboard.

Usage:
    python run_source_recovery.py
    python run_source_recovery.py --dry-run
"""
import sys

from db import get_db
from hackathon_source_recovery_runner import run_source_recovery


def _print_event(entry):
    print(entry["name"])
    if entry["result"] == "eligible":
        print("  eligible for source recovery (dry run - not searched)")
    elif entry["result"] == "processed":
        print(f"  candidates found: {entry['candidates_found']}")
        print(f"  inserted: {entry['candidates_inserted']}, duplicates skipped: {entry['duplicates_skipped']}")
    elif entry["result"] == "runner_error":
        print(f"  ERROR: {entry['error']}")
    print()


def _print_summary(summary):
    print("Summary")
    print(f"Eligible: {summary['eligible']}")
    print(f"Processed: {summary['processed']}")
    print(f"Candidates found: {summary['candidates_found']}")
    print(f"Candidates inserted: {summary['candidates_inserted']}")
    print(f"Duplicates skipped: {summary['duplicates_skipped']}")
    print(f"Runner errors: {summary['runner_errors']}")


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    title = "Event Radar — Source Recovery" + (" (dry run)" if dry_run else "")
    print(title)
    print()

    conn = get_db()
    result = run_source_recovery(conn, dry_run=dry_run)
    conn.close()

    if not result["events"]:
        print("(no eligible hackathons - none have 3+ consecutive source failures)")
        print()
    for entry in result["events"]:
        _print_event(entry)
    _print_summary(result["summary"])


if __name__ == "__main__":
    main()
