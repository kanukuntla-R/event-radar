"""CLI: run one batch pass of hackathon source checks against the real DB
and real collector. Prints a readable per-event report and summary.

Usage:
    python run_hackathon_checks.py
    python run_hackathon_checks.py --dry-run
"""
import sys

from db import get_db
from hackathon_check_runner import run_due_hackathon_checks


def _proposals_line(entry):
    parts = []
    if entry["proposals_inserted"]:
        parts.append(f"{entry['proposals_inserted']} new")
    if entry["proposals_skipped_duplicate"]:
        parts.append(f"{entry['proposals_skipped_duplicate']} duplicate")
    return f"  proposals: {', '.join(parts)}" if parts else "  proposals: 0"


def _print_event(entry):
    print(entry["name"])
    if entry["result"] == "skipped":
        print(f"  skipped: {entry['strategy_reason']}")
        if entry["next_check"]:
            print(f"  next check: {entry['next_check']}")
    elif entry["result"] == "would_check":
        print(f"  would check: {entry['strategy_reason']}")
    elif entry["result"] == "checked":
        print("  checked")
        print(f"  source: {entry['source_result']}")
        print(_proposals_line(entry))
    elif entry["result"] == "runner_error":
        print(f"  ERROR: {entry['error']}")
    print()


def _print_summary(summary):
    print("Summary")
    print(f"Active hackathons: {summary['total_active_hackathons']}")
    print(f"Due: {summary['due']}")
    if "would_check" in summary:
        print(f"Would check: {summary['would_check']}")
    else:
        print(f"Checked: {summary['checked']}")
    print(f"Skipped: {summary['skipped']}")
    print(f"Source success: {summary['source_success']}")
    print(f"Source failure: {summary['source_failure']}")
    print(f"Runner errors: {summary['runner_errors']}")
    print(f"New proposals: {summary['proposals_inserted']}")
    print(f"Duplicate proposals skipped: {summary['proposals_skipped_duplicate']}")


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    title = "Event Radar — Hackathon Check" + (" (dry run)" if dry_run else "")
    print(title)
    print()

    conn = get_db()
    result = run_due_hackathon_checks(conn, dry_run=dry_run)
    conn.close()

    for entry in result["events"]:
        _print_event(entry)
    _print_summary(result["summary"])


if __name__ == "__main__":
    main()
