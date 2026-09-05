"""Read-only CLI: fetch one hackathon's official source and print what the
collector found. Never writes to the database.

Usage:
    python collect_hackathons.py MHacks
    python collect_hackathons.py "Cal Hacks"
"""
import json
import sys

from db import get_db
from hackathon_collector import collect


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

    result = collect(event)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
