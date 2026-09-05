import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "events.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


NEW_COLUMNS = {
    "start_time": "TEXT",
    "end_time": "TEXT",
    "timezone": "TEXT",
    "organizer": "TEXT",
    "registration_status": "TEXT",
    "registration_url": "TEXT",
    "application_deadline": "TEXT",
    "last_checked": "TEXT",
    "travel_required": "INTEGER",
    "event_state": "TEXT NOT NULL DEFAULT 'active' CHECK (event_state IN ('active', 'inactive'))",
    "inactive_reason": "TEXT",
    "last_check_status": "TEXT",
    "consecutive_failures": "INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0)",
    "last_successful_check": "TEXT",
}


def _migrate(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    for name, coltype in NEW_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} {coltype}")


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    conn.commit()
    conn.close()
