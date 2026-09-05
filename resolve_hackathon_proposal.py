"""CLI: list, accept, or dismiss event_change_proposals. Accepting writes the
single proposed field to the events table; dismissing never touches it.

Usage:
    python resolve_hackathon_proposal.py list MHacks
    python resolve_hackathon_proposal.py accept <proposal_id>
    python resolve_hackathon_proposal.py dismiss <proposal_id>
"""
import sys

from db import get_db
from proposal_resolver import accept_proposal, dismiss_proposal


def _usage():
    print(
        f"Usage:\n"
        f"  python {sys.argv[0]} list <event name>\n"
        f"  python {sys.argv[0]} accept <proposal_id>\n"
        f"  python {sys.argv[0]} dismiss <proposal_id>"
    )
    sys.exit(1)


def _list(conn, name):
    event = conn.execute("SELECT id FROM events WHERE name = ?", (name,)).fetchone()
    if event is None:
        print(f"No event named {name!r} found.")
        sys.exit(1)
    rows = conn.execute(
        "SELECT * FROM event_change_proposals WHERE event_id = ? ORDER BY id",
        (event["id"],),
    ).fetchall()
    if not rows:
        print("(no proposals)")
        return
    for row in rows:
        print(
            f"[{row['id']}] {row['status']:9} {row['field']}: "
            f"{row['old_value']!r} -> {row['new_value']!r} "
            f"(detected {row['detected_at']}, resolved {row['resolved_at']})"
        )


def main():
    if len(sys.argv) < 3:
        _usage()

    command, arg = sys.argv[1], sys.argv[2]
    conn = get_db()

    if command == "list":
        _list(conn, arg)
    elif command == "accept":
        accept_proposal(conn, int(arg))
        print(f"Accepted proposal {arg}.")
    elif command == "dismiss":
        dismiss_proposal(conn, int(arg))
        print(f"Dismissed proposal {arg}.")
    else:
        _usage()

    conn.close()


if __name__ == "__main__":
    main()
