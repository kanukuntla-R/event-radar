"""Data-access helpers for the V0.10A multi-user foundation: users,
per-user event status, and calendar tokens. events stays shared - these
helpers never read or write events.status, which remains legacy
single-user state until the app fully switches over (see db.py).
"""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash, check_password_hash

CALENDAR_TOKEN_PREFIX_LENGTH = 8

BOOTSTRAP_OWNER_EMAIL = "owner@event-radar.local"
BOOTSTRAP_OWNER_DISPLAY_NAME = "Owner"

ALLOWED_STATUSES = {"new", "interested", "watching", "ignored"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email):
    """Application-level email identity: strip surrounding whitespace and
    lowercase. schema.sql also enforces this at the DB level via a unique
    index on lower(email), so a caller that skips this still can't create
    a case-variant duplicate - it just gets a clearer error here first."""
    return email.strip().lower()


def get_user_by_email(conn, email):
    return conn.execute(
        "SELECT * FROM users WHERE email = ?", (normalize_email(email),)
    ).fetchone()


def get_user_by_id(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


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
        "INSERT INTO users (email, display_name, password_hash, is_admin, created_at, updated_at) "
        "VALUES (?, ?, NULL, 1, ?, ?)",
        (BOOTSTRAP_OWNER_EMAIL, BOOTSTRAP_OWNER_DISPLAY_NAME, now, now),
    )
    return get_user_by_email(conn, BOOTSTRAP_OWNER_EMAIL)


def create_user(conn, email, password, display_name=None, is_admin=False):
    """Creates a regular (non-admin by default) account with a hashed
    password. Does NOT commit - caller owns the transaction. Rejects a
    duplicate normalized email clearly instead of relying solely on the
    DB's unique index to raise a raw IntegrityError."""
    normalized = normalize_email(email)
    if get_user_by_email(conn, normalized) is not None:
        raise ValueError(f"A user with email {normalized!r} already exists")
    now = _now()
    conn.execute(
        "INSERT INTO users (email, display_name, password_hash, is_admin, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (normalized, display_name, generate_password_hash(password), 1 if is_admin else 0, now, now),
    )
    return get_user_by_email(conn, normalized)


def set_user_password(conn, user_id, password):
    """Hashes and stores a new password for an existing user (including
    one whose password_hash is currently NULL, i.e. account activation).
    Does NOT commit - caller owns the transaction."""
    if not _user_exists(conn, user_id):
        raise ValueError(f"No such user: {user_id!r}")
    conn.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (generate_password_hash(password), _now(), user_id),
    )


def verify_user_password(conn, email, password):
    """Returns the user row on success, or None on any failure - wrong
    password, nonexistent email, or a NULL password_hash (an account that
    has never been activated can never authenticate, regardless of what
    password is tried)."""
    user = get_user_by_email(conn, email)
    if user is None or user["password_hash"] is None:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


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


def hash_calendar_token(raw_token):
    """SHA-256 lookup hash for a calendar bearer token. Not password
    hashing - a calendar token already carries 256 bits of entropy
    (secrets.token_urlsafe(32)), so a fast, deterministic hash is the
    right tool for exact-match lookup; werkzeug's slow password hashing
    would be pointless overhead here and isn't designed for lookup at all
    (its salt makes lookup-by-hash impossible in the first place)."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def get_active_calendar_token_for_user(conn, user_id):
    return conn.execute(
        "SELECT * FROM calendar_tokens WHERE user_id = ? AND revoked_at IS NULL",
        (user_id,),
    ).fetchone()


def get_user_for_calendar_token(conn, raw_token):
    """Resolves the owning user for a raw bearer token. Returns None for
    every failure case alike - nonexistent, revoked, or empty/malformed -
    callers must never distinguish these in a response (see app.py's
    private ICS route: always a generic 404)."""
    if not raw_token:
        return None
    return conn.execute(
        "SELECT u.* FROM calendar_tokens ct JOIN users u ON u.id = ct.user_id "
        "WHERE ct.token_hash = ? AND ct.revoked_at IS NULL",
        (hash_calendar_token(raw_token),),
    ).fetchone()


def _generate_and_insert_token(conn, user_id, max_attempts=5):
    """Inserts a new token row - does NOT commit, caller owns the
    transaction (this matters for rotate_calendar_token, which must
    revoke-then-insert as one atomic unit). Retries only on a genuine
    token_hash collision (astronomically unlikely - 256 bits of
    randomness); any other IntegrityError (e.g. the active-per-user
    partial index, on a genuine concurrent double-create) propagates
    immediately instead of being silently retried."""
    for _ in range(max_attempts):
        raw_token = secrets.token_urlsafe(32)
        try:
            conn.execute(
                "INSERT INTO calendar_tokens (user_id, token_hash, token_prefix, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, hash_calendar_token(raw_token), raw_token[:CALENDAR_TOKEN_PREFIX_LENGTH], _now()),
            )
            return raw_token
        except sqlite3.IntegrityError as e:
            if "calendar_tokens.token_hash" not in str(e):
                raise
            # A fresh INSERT that failed before any commit needs no
            # rollback - just try a new random token.
    raise RuntimeError("Could not generate a unique calendar token")


def create_calendar_token(conn, user_id):
    """Creates a user's first/only active calendar token. Raises
    ValueError if the user doesn't exist or already has an active token
    (use rotate_calendar_token to replace one). Returns the RAW token -
    this is the only time it is ever available; only its hash is stored."""
    if not _user_exists(conn, user_id):
        raise ValueError(f"No such user: {user_id!r}")
    if get_active_calendar_token_for_user(conn, user_id) is not None:
        raise ValueError(f"User {user_id!r} already has an active calendar token")
    try:
        raw_token = _generate_and_insert_token(conn, user_id)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return raw_token


def rotate_calendar_token(conn, user_id):
    """Atomically revokes the user's current active token (if any) and
    creates a new one - one commit covers both, so a failure partway
    through can never leave the user with zero active tokens. Returns
    the new RAW token."""
    if not _user_exists(conn, user_id):
        raise ValueError(f"No such user: {user_id!r}")
    try:
        conn.execute(
            "UPDATE calendar_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_now(), user_id),
        )
        raw_token = _generate_and_insert_token(conn, user_id)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return raw_token


def revoke_calendar_token(conn, user_id):
    """Revokes the user's active token, if any. Idempotent - returns
    False (no-op) when there is no active token to revoke."""
    if not _user_exists(conn, user_id):
        raise ValueError(f"No such user: {user_id!r}")
    cur = conn.execute(
        "UPDATE calendar_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (_now(), user_id),
    )
    conn.commit()
    return cur.rowcount > 0
