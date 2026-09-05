"""Manual inspection tool: run hackathon_strategy against the real DB and
print each event's decision. Read-only - does not write anything back.

Usage: python check_hackathons.py [YYYY-MM-DD]   (defaults to today)
"""
import sys
from datetime import date

from db import get_db
from hackathon_strategy import evaluate


def main():
    today = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()

    conn = get_db()
    events = conn.execute(
        "SELECT * FROM events WHERE event_type = 'hackathon' ORDER BY name"
    ).fetchall()
    conn.close()

    print(f"Evaluating {len(events)} hackathons as of {today.isoformat()}\n")
    header = f"{'name':<28} {'date_status':<10} {'registration':<12} {'should_check':<13} {'reason':<32} next_check"
    print(header)
    print("-" * len(header))
    for e in events:
        result = evaluate(e, today)
        print(
            f"{e['name']:<28} {e['date_status']:<10} {(e['registration_status'] or ''):<12} "
            f"{str(result['should_check']):<13} {result['reason']:<32} {result['next_check'] or ''}"
        )


if __name__ == "__main__":
    main()
