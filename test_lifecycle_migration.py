"""Tests for the V0.3B lifecycle/source-health schema fields:
event_state, inactive_reason, last_check_status, consecutive_failures,
last_successful_check.

Runs entirely against temporary SQLite files (db.DB_PATH is monkeypatched) -
never touches the real data/events.db. Stdlib unittest only.

Run with: python -m unittest test_lifecycle_migration -v
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db as db_module
import seed as seed_module
import user_store

SCHEMA_PATH_TEXT = db_module.SCHEMA_PATH.read_text()

# The exact V0.2 schema (commit 2355e69), i.e. everything before this
# feature's 5 new columns existed - used to simulate a real upgrade.
PRE_V0_3B_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    event_type TEXT,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    start_time TEXT,
    end_time TEXT,
    timezone TEXT,
    date_status TEXT NOT NULL DEFAULT 'confirmed' CHECK (date_status IN ('confirmed', 'tba')),
    location TEXT,
    location_type TEXT,
    source TEXT,
    source_url TEXT,
    organizer TEXT,
    registration_status TEXT,
    registration_url TEXT,
    application_deadline TEXT,
    last_checked TEXT,
    travel_required INTEGER,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'interested', 'watching', 'ignored')),
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

NEW_LIFECYCLE_COLUMNS = (
    "event_state",
    "inactive_reason",
    "last_check_status",
    "consecutive_failures",
    "last_successful_check",
)

# event_review_candidates as it existed before V0.9B.2 added `all_day` -
# used to simulate a real upgrade of an already-live review queue.
PRE_V0_9B2_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_review_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    event_type TEXT,
    event_subtype TEXT,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    start_time TEXT,
    end_time TEXT,
    timezone TEXT,
    location TEXT,
    location_type TEXT,
    location_area TEXT,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    organizer TEXT,
    registration_status TEXT,
    registration_url TEXT,
    application_deadline TEXT,
    review_reason TEXT,
    review_signals TEXT,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'dismissed')),
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class TempDbTestCase(unittest.TestCase):
    """Points db.DB_PATH at a throwaway file for the duration of each test."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "events.db"
        self._orig_db_path = db_module.DB_PATH
        db_module.DB_PATH = self.db_path

    def tearDown(self):
        db_module.DB_PATH = self._orig_db_path
        self.tmpdir.cleanup()

    def _columns(self, conn):
        return {row["name"] for row in conn.execute("PRAGMA table_info(events)")}


class MigrationTests(TempDbTestCase):
    def test_old_database_receives_new_columns_safely(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_3B_SCHEMA)
        conn.execute(
            "INSERT INTO events (name, status) VALUES ('MHacks', 'watching')"
        )
        conn.commit()
        conn.close()

        db_module.init_db()

        conn = db_module.get_db()
        columns = self._columns(conn)
        for col in NEW_LIFECYCLE_COLUMNS:
            self.assertIn(col, columns)

        row = conn.execute("SELECT * FROM events WHERE name = 'MHacks'").fetchone()
        conn.close()

        # existing status preserved - migration only adds columns
        self.assertEqual(row["status"], "watching")
        self.assertEqual(row["event_state"], "active")
        self.assertIsNone(row["inactive_reason"])
        self.assertIsNone(row["last_check_status"])
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertIsNone(row["last_successful_check"])

    def test_migration_is_idempotent(self):
        db_module.init_db()
        db_module.init_db()
        db_module.init_db()  # must not error or add duplicate columns

        conn = db_module.get_db()
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(events)")]
        conn.close()

        self.assertEqual(len(columns), len(set(columns)))
        for col in NEW_LIFECYCLE_COLUMNS:
            self.assertEqual(columns.count(col), 1)

    def test_existing_21_hackathons_remain_active_after_migration(self):
        # Build a V0.2-style DB (pre-lifecycle-columns) with the 21 real
        # hackathons already seeded, then migrate it forward.
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_3B_SCHEMA)
        for event in seed_module.HACKATHONS:
            conn.execute(
                "INSERT INTO events (name, organizer, date_status, registration_status) "
                "VALUES (:name, :organizer, :date_status, 'unknown')",
                event,
            )
        conn.commit()
        conn.close()

        db_module.init_db()

        conn = db_module.get_db()
        rows = conn.execute("SELECT * FROM events").fetchall()
        conn.close()

        self.assertEqual(len(rows), 21)
        for row in rows:
            self.assertEqual(row["event_state"], "active")
            self.assertEqual(row["consecutive_failures"], 0)
            self.assertIsNone(row["inactive_reason"])
            self.assertIsNone(row["last_check_status"])
            self.assertIsNone(row["last_successful_check"])


class ReviewCandidateMigrationTests(TempDbTestCase):
    """V0.9B.2: event_review_candidates gained an `all_day` column on an
    already-live table - must migrate safely without dropping/recreating
    the table or disturbing existing rows."""

    def _review_columns(self, conn):
        return {row["name"] for row in conn.execute("PRAGMA table_info(event_review_candidates)")}

    def test_migration_adds_all_day_column_to_existing_review_table(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_9B2_REVIEW_SCHEMA)
        conn.close()

        db_module.init_db()

        conn = db_module.get_db()
        columns = self._review_columns(conn)
        conn.close()
        self.assertIn("all_day", columns)

    def test_migration_preserves_existing_review_rows_and_statuses(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_9B2_REVIEW_SCHEMA)
        conn.execute(
            "INSERT INTO event_review_candidates "
            "(name, source, source_url, discovered_at, status) "
            "VALUES ('Maosho', 'ASU Events', 'https://asuevents.asu.edu/event/maosho', "
            "'2026-09-05T00:00:00+00:00', 'dismissed')"
        )
        conn.commit()
        conn.close()

        db_module.init_db()

        conn = db_module.get_db()
        row = conn.execute("SELECT * FROM event_review_candidates WHERE name = 'Maosho'").fetchone()
        conn.close()

        self.assertEqual(row["status"], "dismissed")
        self.assertEqual(row["source_url"], "https://asuevents.asu.edu/event/maosho")
        self.assertEqual(row["all_day"], 0)  # safe default - confirmed no NULL-start_time rows live

    def test_review_migration_is_idempotent(self):
        db_module.init_db()
        db_module.init_db()
        db_module.init_db()

        conn = db_module.get_db()
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(event_review_candidates)")]
        conn.close()

        self.assertEqual(columns.count("all_day"), 1)


# The full pre-V0.10A schema (events + proposals + recovery + review
# candidates, no users/user_event_status/calendar_tokens) - used to
# simulate migrating a real, already-live installation forward.
PRE_V0_10A_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    event_type TEXT,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    start_time TEXT,
    end_time TEXT,
    timezone TEXT,
    date_status TEXT NOT NULL DEFAULT 'confirmed' CHECK (date_status IN ('confirmed', 'tba')),
    location TEXT,
    location_type TEXT,
    source TEXT,
    source_url TEXT,
    organizer TEXT,
    registration_status TEXT,
    registration_url TEXT,
    application_deadline TEXT,
    last_checked TEXT,
    travel_required INTEGER,
    event_state TEXT NOT NULL DEFAULT 'active' CHECK (event_state IN ('active', 'inactive')),
    inactive_reason TEXT,
    last_check_status TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    last_successful_check TEXT,
    event_subtype TEXT,
    location_area TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'interested', 'watching', 'ignored')),
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_change_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    evidence TEXT,
    source_result TEXT,
    detected_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'dismissed')),
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS source_recovery_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    candidate_url TEXT NOT NULL,
    candidate_type TEXT NOT NULL CHECK (candidate_type IN (
        'official_event', 'official_organizer', 'official_university',
        'mlh', 'official_social', 'other'
    )),
    title TEXT,
    evidence TEXT,
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'dismissed')),
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_review_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    event_type TEXT,
    event_subtype TEXT,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    start_time TEXT,
    end_time TEXT,
    timezone TEXT,
    location TEXT,
    location_type TEXT,
    location_area TEXT,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    organizer TEXT,
    registration_status TEXT,
    registration_url TEXT,
    application_deadline TEXT,
    all_day INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1)),
    review_reason TEXT,
    review_signals TEXT,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'dismissed')),
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class UserFoundationMigrationTests(TempDbTestCase):
    """V0.10A: users / user_event_status / calendar_tokens foundation.
    events.status must survive untouched; user_event_status is seeded
    once, for a bootstrap owner, from events.status - and never
    overwritten again once a row exists."""

    def _seed_pre_v0_10a_db(self, events):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_10A_SCHEMA)
        for event in events:
            columns = ", ".join(event.keys())
            placeholders = ", ".join(f":{k}" for k in event)
            conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", event)
        conn.commit()
        conn.close()

    def _owner_id(self, conn):
        return conn.execute(
            "SELECT id FROM users WHERE email = 'owner@event-radar.local'"
        ).fetchone()["id"]

    def test_new_tables_created_on_fresh_db(self):
        db_module.init_db()
        conn = db_module.get_db()
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        for table in ("users", "user_event_status", "calendar_tokens"):
            self.assertIn(table, tables)

    def test_bootstrap_owner_created_exactly_once(self):
        self._seed_pre_v0_10a_db([{"name": "MHacks", "status": "watching"}])
        db_module.init_db()
        db_module.init_db()  # rerun - must not create a second owner

        conn = db_module.get_db()
        owners = conn.execute("SELECT * FROM users WHERE email = 'owner@event-radar.local'").fetchall()
        conn.close()
        self.assertEqual(len(owners), 1)

    def test_every_existing_event_gets_one_preserved_status_row(self):
        self._seed_pre_v0_10a_db([
            {"name": "A", "status": "new"},
            {"name": "B", "status": "interested"},
            {"name": "C", "status": "watching"},
            {"name": "D", "status": "ignored"},
        ])
        db_module.init_db()

        conn = db_module.get_db()
        owner_id = self._owner_id(conn)
        rows = conn.execute(
            "SELECT e.name, ues.status FROM events e "
            "JOIN user_event_status ues ON ues.event_id = e.id AND ues.user_id = ?",
            (owner_id,),
        ).fetchall()
        conn.close()

        self.assertEqual(
            {row["name"]: row["status"] for row in rows},
            {"A": "new", "B": "interested", "C": "watching", "D": "ignored"},
        )

    def test_migration_is_idempotent(self):
        self._seed_pre_v0_10a_db([{"name": "A", "status": "interested"}])
        db_module.init_db()
        db_module.init_db()
        db_module.init_db()

        conn = db_module.get_db()
        count = conn.execute("SELECT COUNT(*) FROM user_event_status").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_rerunning_migration_does_not_overwrite_changed_user_event_status(self):
        self._seed_pre_v0_10a_db([{"name": "A", "status": "interested"}])
        db_module.init_db()

        conn = db_module.get_db()
        owner_id = self._owner_id(conn)
        event_id = conn.execute("SELECT id FROM events WHERE name = 'A'").fetchone()["id"]
        conn.execute(
            "UPDATE user_event_status SET status = 'ignored' WHERE user_id = ? AND event_id = ?",
            (owner_id, event_id),
        )
        conn.commit()
        conn.close()

        db_module.init_db()  # rerun - must leave the changed row alone

        conn = db_module.get_db()
        status = conn.execute(
            "SELECT status FROM user_event_status WHERE user_id = ? AND event_id = ?",
            (owner_id, event_id),
        ).fetchone()["status"]
        conn.close()
        self.assertEqual(status, "ignored")

    def test_existing_event_rows_unchanged_by_migration(self):
        self._seed_pre_v0_10a_db([{"name": "A", "status": "interested", "source": "ASU Events"}])
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        before = dict(conn.execute("SELECT * FROM events WHERE name = 'A'").fetchone())
        conn.close()

        db_module.init_db()

        conn = db_module.get_db()
        after = dict(conn.execute("SELECT * FROM events WHERE name = 'A'").fetchone())
        conn.close()
        for key in before:
            self.assertEqual(before[key], after[key], key)

    def test_review_and_proposal_tables_remain_intact(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_10A_SCHEMA)
        conn.execute(
            "INSERT INTO event_review_candidates "
            "(name, source, source_url, discovered_at, status) "
            "VALUES ('Maosho', 'ASU Events', 'https://asuevents.asu.edu/event/maosho', "
            "'2026-09-05T00:00:00+00:00', 'pending')"
        )
        conn.commit()
        conn.close()

        db_module.init_db()

        conn = db_module.get_db()
        row = conn.execute("SELECT * FROM event_review_candidates WHERE name = 'Maosho'").fetchone()
        conn.close()
        self.assertEqual(row["status"], "pending")

    def test_inserting_a_bare_event_does_not_auto_create_status_row(self):
        # Only the migration step backfills user_event_status - it isn't a
        # DB trigger, so a plain INSERT between migration runs shouldn't
        # spontaneously get one.
        db_module.init_db()
        conn = db_module.get_db()
        conn.execute("INSERT INTO events (name, status) VALUES ('New Event', 'new')")
        conn.commit()
        event_id = conn.execute("SELECT id FROM events WHERE name = 'New Event'").fetchone()["id"]
        count = conn.execute(
            "SELECT COUNT(*) FROM user_event_status WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_new_event_after_migration_gets_no_row_even_after_another_init_db(self):
        # V0.10B: the legacy-status backfill is a one-time installation
        # migration, not perpetual sync - a service restart (another
        # init_db() call) must not sweep up events discovered afterward.
        self._seed_pre_v0_10a_db([{"name": "A", "status": "interested"}])
        db_module.init_db()

        conn = db_module.get_db()
        conn.execute("INSERT INTO events (name, status) VALUES ('New Event', 'new')")
        conn.commit()
        conn.close()

        db_module.init_db()  # simulates a service restart

        conn = db_module.get_db()
        event_id = conn.execute("SELECT id FROM events WHERE name = 'New Event'").fetchone()["id"]
        count = conn.execute(
            "SELECT COUNT(*) FROM user_event_status WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class _FailOnMarkerConn:
    """Wraps a real connection but fails the app_migrations marker INSERT -
    proves an interrupted migration can't leave a false completed marker."""

    def __init__(self, real_conn):
        self._real = real_conn

    def execute(self, sql, params=()):
        if "INSERT INTO app_migrations" in sql:
            raise sqlite3.OperationalError("simulated failure")
        return self._real.execute(sql, params)

    def commit(self):
        self._real.commit()

    def rollback(self):
        self._real.rollback()


class MigrationMarkerTests(TempDbTestCase):
    """V0.10B: the legacy-status backfill is guarded by an explicit
    app_migrations marker row, not the fragile "does user_event_status
    have any rows" proxy - which couldn't tell a genuinely-empty
    installation from an interrupted migration."""

    MIGRATION_NAME = db_module.USER_EVENT_STATUS_MIGRATION_NAME

    def _seed_pre_v0_10a_db(self, events):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_10A_SCHEMA)
        for event in events:
            columns = ", ".join(event.keys())
            placeholders = ", ".join(f":{k}" for k in event)
            conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", event)
        conn.commit()
        conn.close()

    def test_marker_written_exactly_once(self):
        self._seed_pre_v0_10a_db([{"name": "A", "status": "interested"}])
        db_module.init_db()
        db_module.init_db()

        conn = db_module.get_db()
        rows = conn.execute(
            "SELECT * FROM app_migrations WHERE name = ?", (self.MIGRATION_NAME,)
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)

    def test_fresh_db_gets_marker_and_no_orphan_rows(self):
        db_module.init_db()
        conn = db_module.get_db()
        row = conn.execute(
            "SELECT * FROM app_migrations WHERE name = ?", (self.MIGRATION_NAME,)
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM user_event_status").fetchone()[0]
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(count, 0)

    def test_interrupted_migration_leaves_no_partially_committed_state(self):
        # Full transaction-boundary audit: a pre-V0.10A DB (no users, no
        # user_event_status, no marker, one legacy event) where the
        # marker INSERT fails must leave ALL THREE migration steps
        # uncommitted - not just the marker/backfill, but also step 1
        # (bootstrap owner creation), which used to commit on its own via
        # get_or_create_bootstrap_owner's internal conn.commit().
        self._seed_pre_v0_10a_db([{"name": "A", "status": "interested"}])
        conn = db_module.get_db()
        conn.executescript(SCHEMA_PATH_TEXT)
        db_module._migrate(conn)
        before_event = dict(conn.execute("SELECT * FROM events WHERE name = 'A'").fetchone())
        flaky = _FailOnMarkerConn(conn)

        with self.assertRaises(sqlite3.OperationalError):
            db_module._migrate_user_event_status(flaky)

        owner_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        backfilled = conn.execute("SELECT COUNT(*) FROM user_event_status").fetchone()[0]
        marker = conn.execute(
            "SELECT * FROM app_migrations WHERE name = ?", (self.MIGRATION_NAME,)
        ).fetchone()
        after_event = dict(conn.execute("SELECT * FROM events WHERE name = 'A'").fetchone())
        conn.close()

        self.assertEqual(owner_count, 0, "bootstrap owner must not survive a failed migration")
        self.assertEqual(backfilled, 0)
        self.assertIsNone(marker)
        self.assertEqual(before_event, after_event)

    def test_retrying_after_interruption_succeeds_and_backfills_correctly(self):
        self._seed_pre_v0_10a_db([{"name": "A", "status": "interested"}])
        conn = db_module.get_db()
        conn.executescript(SCHEMA_PATH_TEXT)
        db_module._migrate(conn)
        flaky = _FailOnMarkerConn(conn)
        with self.assertRaises(sqlite3.OperationalError):
            db_module._migrate_user_event_status(flaky)
        conn.close()

        db_module.init_db()  # real retry, unwrapped

        conn = db_module.get_db()
        owners = conn.execute("SELECT * FROM users WHERE email = 'owner@event-radar.local'").fetchall()
        markers = conn.execute(
            "SELECT * FROM app_migrations WHERE name = ?", (self.MIGRATION_NAME,)
        ).fetchall()
        status = conn.execute(
            "SELECT ues.status FROM user_event_status ues JOIN events e ON e.id = ues.event_id "
            "WHERE e.name = 'A' AND ues.user_id = ?", (owners[0]["id"],),
        ).fetchone()["status"]
        conn.close()
        self.assertEqual(len(owners), 1)
        self.assertEqual(len(markers), 1)
        self.assertEqual(status, "interested")


# The full pre-V0.10C schema (V0.10A/B shape: users has no is_admin
# column, no lower(email) unique index) - used to simulate migrating the
# actual currently-live installation forward.
PRE_V0_10C_SCHEMA = PRE_V0_10A_SCHEMA + """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    password_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_event_status (
    user_id INTEGER NOT NULL REFERENCES users(id),
    event_id INTEGER NOT NULL REFERENCES events(id),
    status TEXT NOT NULL CHECK (status IN ('new', 'interested', 'watching', 'ignored')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, event_id)
);

CREATE TABLE IF NOT EXISTS calendar_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS app_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


class _FailOnAdminMarkerConn:
    """Wraps a real connection but fails the admin-role migration's own
    marker INSERT - proves that failure can't leave a false marker."""

    def __init__(self, real_conn):
        self._real = real_conn

    def execute(self, sql, params=()):
        if "INSERT INTO app_migrations" in sql and db_module.BOOTSTRAP_OWNER_ADMIN_MIGRATION_NAME in params:
            raise sqlite3.OperationalError("simulated failure")
        return self._real.execute(sql, params)

    def commit(self):
        self._real.commit()

    def rollback(self):
        self._real.rollback()


class AdminRoleMigrationTests(TempDbTestCase):
    """V0.10C: the bootstrap owner becomes admin via a one-time,
    email-matched migration - the only place outside
    get_or_create_bootstrap_owner() allowed to grant admin by email."""

    ADMIN_MARKER = db_module.BOOTSTRAP_OWNER_ADMIN_MIGRATION_NAME

    def _seed_pre_v0_10c_db(self, events, owner_status="interested"):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_10C_SCHEMA)
        for event in events:
            columns = ", ".join(event.keys())
            placeholders = ", ".join(f":{k}" for k in event)
            conn.execute(f"INSERT INTO events ({columns}) VALUES ({placeholders})", event)
        conn.commit()
        conn.close()

    def test_fresh_db_owner_is_admin(self):
        db_module.init_db()
        conn = db_module.get_db()
        owner = conn.execute(
            "SELECT * FROM users WHERE email = 'owner@event-radar.local'"
        ).fetchone()
        conn.close()
        self.assertEqual(owner["is_admin"], 1)

    def test_old_db_owner_becomes_admin(self):
        self._seed_pre_v0_10c_db([{"name": "A", "status": "interested"}])
        db_module.init_db()

        conn = db_module.get_db()
        owner = conn.execute(
            "SELECT * FROM users WHERE email = 'owner@event-radar.local'"
        ).fetchone()
        conn.close()
        self.assertEqual(owner["is_admin"], 1)

    def test_migration_preserves_owner_statuses_and_events(self):
        self._seed_pre_v0_10c_db([
            {"name": "A", "status": "interested"},
            {"name": "B", "status": "watching"},
        ])
        db_module.init_db()

        conn = db_module.get_db()
        owner_id = conn.execute("SELECT id FROM users WHERE email = 'owner@event-radar.local'").fetchone()["id"]
        rows = conn.execute(
            "SELECT e.name, ues.status FROM events e "
            "JOIN user_event_status ues ON ues.event_id = e.id AND ues.user_id = ?",
            (owner_id,),
        ).fetchall()
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()
        self.assertEqual({row["name"]: row["status"] for row in rows}, {"A": "interested", "B": "watching"})
        self.assertEqual(event_count, 2)

    def test_does_not_invent_password(self):
        self._seed_pre_v0_10c_db([{"name": "A", "status": "interested"}])
        db_module.init_db()
        conn = db_module.get_db()
        owner = conn.execute("SELECT * FROM users WHERE email = 'owner@event-radar.local'").fetchone()
        conn.close()
        self.assertIsNone(owner["password_hash"])

    def test_does_not_create_calendar_token(self):
        self._seed_pre_v0_10c_db([{"name": "A", "status": "interested"}])
        db_module.init_db()
        conn = db_module.get_db()
        count = conn.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_migration_is_idempotent(self):
        self._seed_pre_v0_10c_db([{"name": "A", "status": "interested"}])
        db_module.init_db()
        db_module.init_db()
        db_module.init_db()

        conn = db_module.get_db()
        owners = conn.execute("SELECT * FROM users WHERE email = 'owner@event-radar.local'").fetchall()
        markers = conn.execute(
            "SELECT * FROM app_migrations WHERE name = ?", (self.ADMIN_MARKER,)
        ).fetchall()
        conn.close()
        self.assertEqual(len(owners), 1)
        self.assertEqual(len(markers), 1)

    def test_does_not_grant_admin_to_non_owner_accounts(self):
        self._seed_pre_v0_10c_db([{"name": "A", "status": "interested"}])
        db_module.init_db()

        conn = db_module.get_db()
        from user_store import create_user
        friend = create_user(conn, "friend@example.com", "s3cret-pass")
        conn.commit()
        conn.close()

        db_module.init_db()  # rerun must not touch the friend account

        conn = db_module.get_db()
        row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (friend["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["is_admin"], 0)

    def test_transaction_failure_cannot_leave_false_migration_completion(self):
        # Simulate a genuinely pre-V0.10C owner row: created back when
        # get_or_create_bootstrap_owner didn't set is_admin at all, so the
        # users-table migration's ALTER TABLE ... DEFAULT 0 left it at 0.
        self._seed_pre_v0_10c_db([{"name": "A", "status": "interested"}])
        conn = db_module.get_db()
        conn.executescript(SCHEMA_PATH_TEXT)
        db_module._migrate(conn)
        conn.execute(
            "INSERT INTO users (email, display_name, password_hash, is_admin, created_at, updated_at) "
            "VALUES (?, 'Owner', NULL, 0, datetime('now'), datetime('now'))",
            (db_module.BOOTSTRAP_OWNER_EMAIL,),
        )
        conn.commit()
        flaky = _FailOnAdminMarkerConn(conn)

        with self.assertRaises(sqlite3.OperationalError):
            db_module._migrate_bootstrap_owner_admin(flaky)

        owner = conn.execute("SELECT * FROM users WHERE email = 'owner@event-radar.local'").fetchone()
        marker = conn.execute(
            "SELECT * FROM app_migrations WHERE name = ?", (self.ADMIN_MARKER,)
        ).fetchone()
        conn.close()
        self.assertEqual(owner["is_admin"], 0, "a failed admin migration must not leave is_admin set")
        self.assertIsNone(marker)


class EmailUniquenessMigrationTests(TempDbTestCase):
    """V0.10C: the lower(email) unique index must fail loudly rather than
    silently merge/drop a conflicting pre-existing database."""

    def test_conflicting_case_variant_emails_fail_clearly_instead_of_merging(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_10C_SCHEMA)
        conn.execute(
            "INSERT INTO users (email, display_name, created_at, updated_at) "
            "VALUES ('friend@example.com', 'Friend', datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO users (email, display_name, created_at, updated_at) "
            "VALUES ('Friend@Example.com', 'Friend Dup', datetime('now'), datetime('now'))"
        )
        conn.commit()
        conn.close()

        with self.assertRaises(sqlite3.IntegrityError):
            db_module.init_db()

        # Neither conflicting row was silently merged or deleted.
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) FROM users WHERE email IN (?, ?)",
                              ("friend@example.com", "Friend@Example.com")).fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)

    def test_non_conflicting_existing_db_migrates_cleanly(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_10C_SCHEMA)
        conn.execute(
            "INSERT INTO users (email, display_name, created_at, updated_at) "
            "VALUES ('friend@example.com', 'Friend', datetime('now'), datetime('now'))"
        )
        conn.commit()
        conn.close()

        db_module.init_db()  # must not raise

        conn = db_module.get_db()
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)  # the seeded friend + bootstrap owner


class _FailOnCalendarTokenMarkerConn:
    """Wraps a real connection but fails the calendar-token-hash
    migration's own marker INSERT - proves the table rebuild can't be
    left half-done with a false completed marker."""

    def __init__(self, real_conn):
        self._real = real_conn

    def execute(self, sql, params=()):
        if "INSERT INTO app_migrations" in sql and db_module.CALENDAR_TOKENS_HASH_MIGRATION_NAME in params:
            raise sqlite3.OperationalError("simulated failure")
        return self._real.execute(sql, params)

    def commit(self):
        self._real.commit()

    def rollback(self):
        self._real.rollback()


class CalendarTokenHashMigrationTests(TempDbTestCase):
    """V0.10D: calendar_tokens moves from a raw plaintext token column to
    a SHA-256 lookup hash + short non-secret prefix. Existing
    subscriptions must keep working (the same raw token still resolves
    after migration) and the plaintext must not survive."""

    MARKER = db_module.CALENDAR_TOKENS_HASH_MIGRATION_NAME

    def _seed_legacy_db(self, emails, tokens):
        """tokens: list of (user_email, raw_token, revoked_at_or_None)."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(PRE_V0_10C_SCHEMA)
        for email in emails:
            conn.execute(
                "INSERT INTO users (email, display_name, created_at, updated_at) "
                "VALUES (?, ?, datetime('now'), datetime('now'))",
                (email, email.split("@")[0]),
            )
        for user_email, raw_token, revoked_at in tokens:
            user_id = conn.execute("SELECT id FROM users WHERE email = ?", (user_email,)).fetchone()[0]
            conn.execute(
                "INSERT INTO calendar_tokens (user_id, token, created_at, revoked_at) "
                "VALUES (?, ?, datetime('now'), ?)",
                (user_id, raw_token, revoked_at),
            )
        conn.commit()
        conn.close()

    def _cols(self, conn):
        return {row["name"] for row in conn.execute("PRAGMA table_info(calendar_tokens)")}

    def test_fresh_install_has_hashed_schema_and_zero_tokens(self):
        db_module.init_db()
        conn = db_module.get_db()
        cols = self._cols(conn)
        count = conn.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        marker = conn.execute("SELECT 1 FROM app_migrations WHERE name = ?", (self.MARKER,)).fetchone()
        conn.close()
        self.assertIn("token_hash", cols)
        self.assertNotIn("token", cols)
        self.assertEqual(count, 0)
        self.assertIsNotNone(marker)

    def test_legacy_db_with_zero_tokens_migrates_cleanly(self):
        self._seed_legacy_db(["friend@example.com"], [])
        db_module.init_db()  # must not raise
        conn = db_module.get_db()
        self.assertIn("token_hash", self._cols(conn))
        conn.close()

    def test_legacy_active_token_still_authenticates_after_migration(self):
        self._seed_legacy_db(["friend@example.com"], [("friend@example.com", "legacy-raw-token-abc123", None)])
        db_module.init_db()

        conn = db_module.get_db()
        user = user_store.get_user_for_calendar_token(conn, "legacy-raw-token-abc123")
        conn.close()
        self.assertIsNotNone(user)
        self.assertEqual(user["email"], "friend@example.com")

    def test_plaintext_token_does_not_survive_migration(self):
        self._seed_legacy_db(["friend@example.com"], [("friend@example.com", "legacy-raw-token-abc123", None)])
        db_module.init_db()

        conn = db_module.get_db()
        rows = conn.execute("SELECT * FROM calendar_tokens").fetchall()
        conn.close()
        for row in rows:
            self.assertNotIn("legacy-raw-token-abc123", [str(v) for v in dict(row).values()])

    def test_legacy_revoked_token_remains_revoked(self):
        self._seed_legacy_db(
            ["friend@example.com"],
            [("friend@example.com", "legacy-revoked-token", "2026-01-01T00:00:00+00:00")],
        )
        db_module.init_db()

        conn = db_module.get_db()
        user = user_store.get_user_for_calendar_token(conn, "legacy-revoked-token")
        row = conn.execute("SELECT * FROM calendar_tokens").fetchone()
        conn.close()
        self.assertIsNone(user)
        self.assertIsNotNone(row["revoked_at"])

    def test_multiple_users_tokens_ownership_preserved(self):
        self._seed_legacy_db(
            ["alice@example.com", "bob@example.com"],
            [("alice@example.com", "alice-raw-token", None), ("bob@example.com", "bob-raw-token", None)],
        )
        db_module.init_db()

        conn = db_module.get_db()
        alice = user_store.get_user_for_calendar_token(conn, "alice-raw-token")
        bob = user_store.get_user_for_calendar_token(conn, "bob-raw-token")
        conn.close()
        self.assertEqual(alice["email"], "alice@example.com")
        self.assertEqual(bob["email"], "bob@example.com")

    def test_ids_and_created_at_preserved(self):
        self._seed_legacy_db(["friend@example.com"], [("friend@example.com", "legacy-raw-token-abc123", None)])
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        before = dict(conn.execute("SELECT id, user_id, created_at FROM calendar_tokens").fetchone())
        conn.close()

        db_module.init_db()

        conn = db_module.get_db()
        after = dict(conn.execute("SELECT id, user_id, created_at FROM calendar_tokens").fetchone())
        conn.close()
        self.assertEqual(before, after)

    def test_rerun_after_migration_makes_no_changes(self):
        self._seed_legacy_db(["friend@example.com"], [("friend@example.com", "legacy-raw-token-abc123", None)])
        db_module.init_db()
        db_module.init_db()
        db_module.init_db()

        conn = db_module.get_db()
        user = user_store.get_user_for_calendar_token(conn, "legacy-raw-token-abc123")
        count = conn.execute("SELECT COUNT(*) FROM calendar_tokens").fetchone()[0]
        markers = conn.execute(
            "SELECT COUNT(*) FROM app_migrations WHERE name = ?", (self.MARKER,)
        ).fetchone()[0]
        conn.close()
        self.assertIsNotNone(user)
        self.assertEqual(count, 1)
        self.assertEqual(markers, 1)

    def test_forced_failure_leaves_old_table_recoverable_and_no_false_marker(self):
        self._seed_legacy_db(["friend@example.com"], [("friend@example.com", "legacy-raw-token-abc123", None)])
        conn = db_module.get_db()
        conn.executescript(SCHEMA_PATH_TEXT)
        db_module._migrate(conn)
        db_module._migrate_user_event_status(conn)
        db_module._migrate_bootstrap_owner_admin(conn)
        flaky = _FailOnCalendarTokenMarkerConn(conn)

        with self.assertRaises(sqlite3.OperationalError):
            db_module._migrate_calendar_tokens_hash(flaky)

        marker = conn.execute("SELECT 1 FROM app_migrations WHERE name = ?", (self.MARKER,)).fetchone()
        conn.close()
        self.assertIsNone(marker)

        # Retry (unwrapped, real connection) must succeed and recover the
        # original subscription.
        db_module.init_db()
        conn = db_module.get_db()
        user = user_store.get_user_for_calendar_token(conn, "legacy-raw-token-abc123")
        markers = conn.execute(
            "SELECT COUNT(*) FROM app_migrations WHERE name = ?", (self.MARKER,)
        ).fetchone()[0]
        conn.close()
        self.assertIsNotNone(user)
        self.assertEqual(markers, 1)


class SeedLifecycleTests(TempDbTestCase):
    def test_freshly_seeded_hackathons_default_to_active(self):
        seed_module.seed()

        conn = db_module.get_db()
        rows = conn.execute("SELECT * FROM events").fetchall()
        conn.close()

        self.assertEqual(len(rows), 21)
        for row in rows:
            self.assertEqual(row["event_state"], "active")
            self.assertIsNone(row["inactive_reason"])
            self.assertIsNone(row["last_check_status"])
            self.assertEqual(row["consecutive_failures"], 0)
            self.assertIsNone(row["last_successful_check"])

    def test_rerunning_seed_does_not_duplicate_or_reset_lifecycle_fields(self):
        seed_module.seed()

        conn = db_module.get_db()
        conn.execute(
            """
            UPDATE events
            SET status = 'watching', event_state = 'inactive',
                inactive_reason = 'manual', last_check_status = 'not_found',
                consecutive_failures = 3, last_successful_check = '2026-08-01'
            WHERE name = 'MHacks'
            """
        )
        conn.commit()
        conn.close()

        seed_module.seed()
        seed_module.seed()  # rerun twice to be sure

        conn = db_module.get_db()
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        mhacks = conn.execute("SELECT * FROM events WHERE name = 'MHacks'").fetchone()
        dupes = conn.execute(
            "SELECT name, COUNT(*) c FROM events GROUP BY name HAVING c > 1"
        ).fetchall()
        conn.close()

        self.assertEqual(count, 21)
        self.assertEqual(dupes, [])
        self.assertEqual(mhacks["status"], "watching")
        self.assertEqual(mhacks["event_state"], "inactive")
        self.assertEqual(mhacks["inactive_reason"], "manual")
        self.assertEqual(mhacks["last_check_status"], "not_found")
        self.assertEqual(mhacks["consecutive_failures"], 3)
        self.assertEqual(mhacks["last_successful_check"], "2026-08-01")


if __name__ == "__main__":
    unittest.main()
