"""CLI: run one batch pass of hackathon source checks against the real DB
and real collector. Prints a readable per-event report and summary.

Invoked by event-radar-hackathons.timer/.service (deploy/systemd/) with NO
arguments, daily at 07:00 - so, unlike run_az_tech_council_discovery.py,
no-argument invocation deliberately still runs a real persistent pass
(dry_run=False). --help/-h and unknown arguments never touch the DB or
network, same safety guarantee as that other CLI - only the no-args default
differs, to preserve the live timer's existing contract. (Note:
event-radar-source-recovery.timer runs 30 minutes later and depends on this
job's consecutive_failures writes already being in place.)

Usage:
    python run_hackathon_checks.py              # persistent run (dry_run=False)
    python run_hackathon_checks.py --dry-run
"""
import argparse
import sys


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


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run one batch pass of hackathon source checks. With no arguments, "
                     "runs a real persistent pass (matches the live systemd timer's contract).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Check due hackathons but write nothing to the database.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    dry_run = args.dry_run

    # Imports deferred until after arg parsing so --help/-h/unknown-arg exits
    # never touch the DB or network.
    from db import get_db
    from hackathon_check_runner import run_due_hackathon_checks

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
