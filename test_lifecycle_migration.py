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
