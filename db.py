import sqlite3
from pathlib import Path

from user_store import get_or_create_bootstrap_owner

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


USER_EVENT_STATUS_MIGRATION_NAME = "v0_10a_bootstrap_user_event_status"


def _migration_applied(conn, name):
    return conn.execute(
        "SELECT 1 FROM app_migrations WHERE name = ?", (name,)
    ).fetchone() is not None


def _migrate_user_event_status(conn):
    """V0.10A backfill of user_event_status from the legacy events.status
    column, for a single bootstrap owner - guarded by an explicit
    app_migrations marker so it runs exactly once ever, not "once per
    event that happens to be missing a row" (V0.10B: the app now treats
    a missing row as effective status 'new', so letting this rerun on
    every init_db() would wrongly backfill every newly discovered event
    too). Transactional: owner creation, the backfill, and the marker all
    commit together or none do - get_or_create_bootstrap_owner doesn't
    commit on its own, so an interrupted run can't leave a false
    "completed" marker, a partial backfill, or an orphaned bootstrap
    owner with no data behind it."""
    if _migration_applied(conn, USER_EVENT_STATUS_MIGRATION_NAME):
        return
    try:
        owner = get_or_create_bootstrap_owner(conn)
        conn.execute(
            "INSERT INTO user_event_status (user_id, event_id, status, created_at, updated_at) "
            "SELECT ?, id, status, datetime('now'), datetime('now') FROM events "
            "WHERE id NOT IN (SELECT event_id FROM user_event_status WHERE user_id = ?)",
            (owner["id"], owner["id"]),
        )
        conn.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES (?, datetime('now'))",
            (USER_EVENT_STATUS_MIGRATION_NAME,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    _migrate_user_event_status(conn)
    conn.commit()
    conn.close()
