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
    "event_subtype": "TEXT",
    "location_area": "TEXT",
}

REVIEW_CANDIDATE_NEW_COLUMNS = {
    # V0.9B.2: without this, a REVIEW candidate's all_day evidence was lost
    # while pending, so accept-time validation could wrongly treat an
    # explicit all-day candidate as an incomplete timed one. See
    # event_review_store.py / event_review_resolver.py.
    "all_day": "INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1))",
}


def _migrate_table(conn, table, new_columns):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, coltype in new_columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


def _migrate(conn):
    _migrate_table(conn, "events", NEW_COLUMNS)
    _migrate_table(conn, "event_review_candidates", REVIEW_CANDIDATE_NEW_COLUMNS)


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    conn.commit()
    conn.close()
