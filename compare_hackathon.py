"""Read-only CLI: fetch one hackathon's official source, compare it against
the stored SQLite record, and print the proposed changes. Never writes to
the database.

Usage:
    python compare_hackathon.py MHacks
    python compare_hackathon.py "Cal Hacks"
"""
import json
import sys

from db import get_db
from hackathon_collector import collect
from hackathon_change_detector import detect_changes


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <event name>")
        sys.exit(1)

    name = sys.argv[1]
    conn = get_db()
    event = conn.execute("SELECT * FROM events WHERE name = ?", (name,)).fetchone()
    conn.close()

    if event is None:
        print(f"No event named {name!r} found.")
        sys.exit(1)

    collector_result = collect(event)
    comparison = detect_changes(event, collector_result)

    print(json.dumps({
        "event": name,
        "collector_result": collector_result,
        "comparison": comparison,
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
