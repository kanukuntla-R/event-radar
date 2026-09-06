"""Data-access helpers for the V0.10A multi-user foundation: users,
per-user event status, and calendar tokens. events stays shared - these
helpers never read or write events.status, which remains legacy
single-user state until the app fully switches over (see db.py).
"""
import secrets
import sqlite3
from datetime import datetime, timezone

BOOTSTRAP_OWNER_EMAIL = "owner@event-radar.local"
BOOTSTRAP_OWNER_DISPLAY_NAME = "Owner"

ALLOWED_STATUSES = {"new", "interested", "watching", "ignored"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_user_by_email(conn, email):
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def _user_exists(conn, user_id):
    return conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None


def _event_exists(conn, event_id):
    return conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is not None


def get_or_create_bootstrap_owner(conn):
    """Returns the single local bootstrap/owner user, creating it if this
    is a fresh or not-yet-migrated installation. Idempotent - looked up
    by email, never inserted twice.

    Does NOT commit - the caller owns the transaction. This matters for
    db.py's migration, which needs owner creation to be part of the same
    atomic commit as its status backfill and migration marker; a caller
    that just wants the owner available immediately (e.g. a Flask route
    reading/writing in the same request) should commit itself, same as
    it already does for whatever it does next on this connection."""
    existing = get_user_by_email(conn, BOOTSTRAP_OWNER_EMAIL)
    if existing is not None:
        return existing
    now = _now()
    conn.execute(
        "INSERT INTO users (email, display_name, password_hash, created_at, updated_at) "
        "VALUES (?, ?, NULL, ?, ?)",
        (BOOTSTRAP_OWNER_EMAIL, BOOTSTRAP_OWNER_DISPLAY_NAME, now, now),
    )
    return get_user_by_email(conn, BOOTSTRAP_OWNER_EMAIL)


def get_user_event_status(conn, user_id, event_id):
    row = conn.execute(
        "SELECT status FROM user_event_status WHERE user_id = ? AND event_id = ?",
        (user_id, event_id),
    ).fetchone()
    return row["status"] if row else None


def set_user_event_status(conn, user_id, event_id, status):
    """Insert-or-update a user's status for an event. Never touches
    events.status. Preserves created_at across updates."""
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")
    if not _user_exists(conn, user_id):
        raise ValueError(f"No such user: {user_id!r}")
    if not _event_exists(conn, event_id):
        raise ValueError(f"No such event: {event_id!r}")
    now = _now()
    existing = conn.execute(
        "SELECT 1 FROM user_event_status WHERE user_id = ? AND event_id = ?",
        (user_id, event_id),
    ).fetchone()
    try:
        if existing is None:
            conn.execute(
                "INSERT INTO user_event_status (user_id, event_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, event_id, status, now, now),
            )
        else:
            conn.execute(
                "UPDATE user_event_status SET status = ?, updated_at = ? "
                "WHERE user_id = ? AND event_id = ?",
                (status, now, user_id, event_id),
            )
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def create_calendar_token(conn, user_id, max_attempts=5):
    """Creates a cryptographically random, unique, opaque calendar token
    for a user. Retries on the astronomically unlikely chance of a
    collision (secrets.token_urlsafe(32) is 256 bits of randomness) -
    but only for that specific collision, never for an unrelated
    integrity error, and never for a nonexistent user (checked once,
    up front, with no retry)."""
    if not _user_exists(conn, user_id):
        raise ValueError(f"No such user: {user_id!r}")
    for _ in range(max_attempts):
        token = secrets.token_urlsafe(32)
        try:
            conn.execute(
                "INSERT INTO calendar_tokens (user_id, token, created_at) VALUES (?, ?, ?)",
                (user_id, token, _now()),
            )
            conn.commit()
            return token
        except sqlite3.IntegrityError as e:
            if "calendar_tokens.token" not in str(e):
                raise
            conn.rollback()
    raise RuntimeError("Could not generate a unique calendar token")
