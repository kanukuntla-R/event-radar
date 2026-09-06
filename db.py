import sqlite3
from pathlib import Path

from user_store import (
    get_or_create_bootstrap_owner, BOOTSTRAP_OWNER_EMAIL,
    hash_calendar_token, CALENDAR_TOKEN_PREFIX_LENGTH,
)

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

USERS_NEW_COLUMNS = {
    # V0.10C: defaults every existing row (including the pre-existing
    # bootstrap owner) to non-admin; _migrate_bootstrap_owner_admin below
    # is the one-time exception that promotes the owner specifically.
    "is_admin": "INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1))",
}


def _migrate_table(conn, table, new_columns):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, coltype in new_columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


def _migrate(conn):
    _migrate_table(conn, "events", NEW_COLUMNS)
    _migrate_table(conn, "event_review_candidates", REVIEW_CANDIDATE_NEW_COLUMNS)
    _migrate_table(conn, "users", USERS_NEW_COLUMNS)


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


BOOTSTRAP_OWNER_ADMIN_MIGRATION_NAME = "v0_10c_bootstrap_owner_admin"


def _migrate_bootstrap_owner_admin(conn):
    """V0.10C: the bootstrap owner becomes admin. This is the ONE other
    place (besides get_or_create_bootstrap_owner's own INSERT) allowed to
    grant is_admin by matching BOOTSTRAP_OWNER_EMAIL - every other
    authorization check in the app must use the is_admin column, never
    infer admin from an email address. Guarded by its own marker and
    transactional, same discipline as _migrate_user_event_status."""
    if _migration_applied(conn, BOOTSTRAP_OWNER_ADMIN_MIGRATION_NAME):
        return
    try:
        conn.execute(
            "UPDATE users SET is_admin = 1, updated_at = datetime('now') WHERE email = ?",
            (BOOTSTRAP_OWNER_EMAIL,),
        )
        conn.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES (?, datetime('now'))",
            (BOOTSTRAP_OWNER_ADMIN_MIGRATION_NAME,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


CALENDAR_TOKENS_HASH_MIGRATION_NAME = "v0_10d_calendar_tokens_hash"


def _calendar_tokens_needs_hash_migration(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(calendar_tokens)")}
    return "token" in cols and "token_hash" not in cols


def _migrate_calendar_tokens_hash(conn):
    """V0.10D: calendar_tokens used to store the raw bearer token in
    plaintext. Rebuilds the table to store only a SHA-256 lookup hash
    (plus a short non-secret prefix) - every existing subscription keeps
    working, since a request's raw token is hashed before lookup, but the
    plaintext itself is discarded. schema.sql's CREATE TABLE IF NOT
    EXISTS never touches an existing (legacy-shaped) table, so this is
    the only path that converts one. Guarded by its own marker;
    transactional - the whole rebuild and the marker commit together or
    neither does, so an interrupted run can't leave a half-rebuilt table
    or a false "completed" marker."""
    if _migration_applied(conn, CALENDAR_TOKENS_HASH_MIGRATION_NAME):
        return
    try:
        # Unlike the other migrations here, this one issues DDL (DROP/
        # CREATE TABLE). Python's sqlite3 module (legacy transaction
        # control, the default) only auto-opens a transaction on DML -
        # DDL runs in autocommit unless a transaction is already open, so
        # without this explicit BEGIN, conn.rollback() below would NOT
        # undo the table rebuild on a failure (verified empirically).
        conn.execute("BEGIN")
        if _calendar_tokens_needs_hash_migration(conn):
            legacy_rows = conn.execute(
                "SELECT id, user_id, token, created_at, revoked_at FROM calendar_tokens"
            ).fetchall()
            # schema.sql's executescript (already run before this function)
            # may have already created the new partial index against this
            # legacy-shaped table (it only references user_id/revoked_at,
            # both present in the old shape too) - dropping the table
            # drops that index along with it, so the CREATE below never
            # collides on the index name.
            conn.execute("DROP TABLE calendar_tokens")
            conn.execute(
                "CREATE TABLE calendar_tokens ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user_id INTEGER NOT NULL REFERENCES users(id), "
                "token_hash TEXT NOT NULL UNIQUE, "
                "token_prefix TEXT, "
                "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "revoked_at TEXT)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX idx_calendar_tokens_active_per_user "
                "ON calendar_tokens (user_id) WHERE revoked_at IS NULL"
            )
            for row in legacy_rows:
                conn.execute(
                    "INSERT INTO calendar_tokens "
                    "(id, user_id, token_hash, token_prefix, created_at, revoked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["id"], row["user_id"], hash_calendar_token(row["token"]),
                        row["token"][:CALENDAR_TOKEN_PREFIX_LENGTH], row["created_at"], row["revoked_at"],
                    ),
                )
        conn.execute(
            "INSERT INTO app_migrations (name, applied_at) VALUES (?, datetime('now'))",
            (CALENDAR_TOKENS_HASH_MIGRATION_NAME,),
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
    _migrate_bootstrap_owner_admin(conn)
    _migrate_calendar_tokens_hash(conn)
    conn.commit()
    conn.close()
