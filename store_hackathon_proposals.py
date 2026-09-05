"""CLI: run a live source check for one hackathon, record source health,
and store any detected fact changes as reviewable proposals. Never applies
proposals to the events table - see proposal_store.py for what's written.

Usage:
    python store_hackathon_proposals.py MHacks
"""
import sys

from db import get_db
from hackathon_collector import collect
from hackathon_change_detector import detect_changes
from check_result_recorder import record_check_result
from proposal_store import store_change_proposals


def _print_pending(conn, event_id, label):
    rows = conn.execute(
        "SELECT field, old_value, new_value, detected_at FROM event_change_proposals "
        "WHERE event_id = ? AND status = 'pending' ORDER BY id",
        (event_id,),
    ).fetchall()
    print(f"{label} ({len(rows)}):")
    if not rows:
        print("  (none)")
    for row in rows:
        print(f"  {row['field']}: {row['old_value']!r} -> {row['new_value']!r} (detected {row['detected_at']})")


def _print_facts(event, label):
    print(f"{label}:")
    for field in ("start_date", "end_date", "date_status", "registration_status",
                  "registration_url", "application_deadline"):
        print(f"  {field:22} = {event[field]!r}")


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

    _print_pending(conn, event["id"], "Pending proposals before this run")

    collector_result = collect(event)
    comparison = detect_changes(event, collector_result)

    record_check_result(conn, event["id"], collector_result)
    outcome = store_change_proposals(conn, event["id"], comparison)

    print(f"\nSource result: {collector_result['source_result']}")
    print(f"Inserted ({len(outcome['inserted'])}):")
    for entry in outcome["inserted"]:
        print(f"  {entry['field']}: {entry['old_value']!r} -> {entry['new_value']!r}")
    print(f"Skipped as duplicate ({len(outcome['skipped_duplicate'])}):")
    for entry in outcome["skipped_duplicate"]:
        print(f"  {entry['field']}: {entry['old_value']!r} -> {entry['new_value']!r}")

    updated_event = conn.execute("SELECT * FROM events WHERE id = ?", (event["id"],)).fetchone()
    conn.close()

    print()
    _print_facts(updated_event, "Event facts after this run (should be unchanged)")
    print(
        "\nNote: event facts above come straight from the events table - "
        "storing proposals never writes to it. Review proposals manually; "
        "nothing is auto-applied."
    )


if __name__ == "__main__":
    main()
