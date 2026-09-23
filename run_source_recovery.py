"""CLI: run one batch pass of source-recovery discovery against the real DB
and real collector. Only considers active hackathons with 3+ consecutive
source failures. Never writes events.source_url - discovered candidates are
stored as pending rows for review in the dashboard.

Invoked by event-radar-source-recovery.timer/.service (deploy/systemd/)
with NO arguments, daily at 07:30 - so, unlike
run_az_tech_council_discovery.py, no-argument invocation deliberately still
runs a real persistent pass (dry_run=False). --help/-h and unknown
arguments never touch the DB or network, same safety guarantee as that
other CLI - only the no-args default differs, to preserve the live timer's
existing contract.

Usage:
    python run_source_recovery.py              # persistent run (dry_run=False)
    python run_source_recovery.py --dry-run
"""
import argparse
import sys


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


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run one batch pass of hackathon source-recovery discovery. With no "
                     "arguments, runs a real persistent pass (matches the live systemd timer's contract).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Find eligible hackathons but do not search for replacement sources or write to the database.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    dry_run = args.dry_run

    # Imports deferred until after arg parsing so --help/-h/unknown-arg exits
    # never touch the DB or network.
    from db import get_db
    from hackathon_source_recovery_runner import run_source_recovery

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
