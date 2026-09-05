"""CLI: run a live source check for one hackathon, show proposed fact
changes for visibility only, then record ONLY source-check health to
SQLite. Event facts (dates, registration_status, etc.) are never written
by this script - see check_result_recorder.py for what's actually updated.

Usage:
    python record_hackathon_check.py MHacks
"""
import sys

from db import get_db
from hackathon_collector import collect
from hackathon_change_detector import detect_changes
from check_result_recorder import record_check_result


def _print_health(event, label):
    print(f"{label}:")
    print(f"  last_checked          = {event['last_checked']}")
    print(f"  last_check_status     = {event['last_check_status']}")
    print(f"  last_successful_check = {event['last_successful_check']}")
    print(f"  consecutive_failures  = {event['consecutive_failures']}")
    print(f"  event_state           = {event['event_state']}")
    print(f"  registration_status   = {event['registration_status']}")
    print(f"  registration_url      = {event['registration_url']}")


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <event name>")
        sys.exit(1)

    name = sys.argv[1]
    conn = get_db()
    event = conn.execute("SELECT * FROM events WHERE name = ?", (name,)).fetchone()
    if event is None:
        print(f"No event named {name!r} found.")
        conn.close()
        sys.exit(1)

    _print_health(event, "Before")

    collector_result = collect(event)
    comparison = detect_changes(event, collector_result)

    print("\nProposed fact changes (NOT applied):")
    if comparison["changes"]:
        for change in comparison["changes"]:
            print(
                f"  {change['field']}: {change['old_value']!r} -> "
                f"{change['new_value']!r} (evidence: {change['evidence']!r})"
            )
    else:
        print("  (none)")

    record_check_result(conn, event["id"], collector_result)

    updated_event = conn.execute("SELECT * FROM events WHERE id = ?", (event["id"],)).fetchone()
    conn.close()

    print()
    _print_health(updated_event, "After")
    print(
        "\nNote: only source-check health fields were written above. Event "
        "facts (dates, registration_status, registration_url, etc.) were "
        "left untouched, even though the collector may have detected changes."
    )


if __name__ == "__main__":
    main()
