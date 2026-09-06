-- registration_status: unknown | not_open | open | closed (no DB constraint yet -
-- nothing writes this field through the app, so it's just documented here)
-- travel_required: 0 = local/remote, 1 = travel needed
--
-- inactive_reason: canceled | removed | source_missing | finished | manual
-- last_check_status: ok | redirected | not_found | unavailable | parse_failed
-- (both documented only, same as registration_status - nothing writes them yet)
--
-- Lifecycle policy for the future source-checking collector (NOT implemented
-- yet - no code sets these automatically today):
--   1st consecutive source failure  -> event_state stays 'active'
--   2nd consecutive source failure  -> event_state stays 'active' (warning-eligible)
--   3rd+ consecutive source failure -> event becomes eligible for manual review/
--                                      inactivation with inactive_reason='source_missing'
-- A failed check must never *automatically* flip event_state to 'inactive' -
-- that only happens on explicit confirmation (event canceled/removed) or a
-- human/collector decision after repeated failures crosses the review threshold.
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
    -- event_subtype: nullable, no CHECK yet (discovery categories will grow).
    -- V0.8A values: career_fair | employer_event | networking | info_session |
    -- workshop | career_services | other
    event_subtype TEXT,
    -- location_area: nullable. V0.8A values: tempe | scottsdale | chandler |
    -- phoenix | mesa | online | hybrid | other (see event_location.py)
    location_area TEXT,
    -- V0.10A: legacy single-user state. Preserved and still read/written by
    -- the app for now; user_event_status (below) is the future per-user
    -- source of truth once the app switches over in a later phase.
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'interested', 'watching', 'ignored')),
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Reviewable proposals for event *fact* changes detected by
-- hackathon_change_detector. Nothing applies these to the events table
-- automatically yet - that's a future accept/dismiss step.
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

-- Candidate replacement source URLs discovered for hackathons with unhealthy
-- sources (consecutive_failures >= 3). Purely advisory - nothing writes
-- events.source_url automatically; only source_recovery_resolver's explicit
-- accept action does that, on human review of a pending row here.
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

-- ASU Events discovery candidates the relevance filter (asu_event_relevance.py)
-- marked "review" - held for human accept/dismiss, never auto-inserted into
-- events. Accepting one runs it through the exact same
-- validation/dedup path as a normal discovered event (see
-- event_review_resolver.py); it is never marked Interested automatically.
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
    -- Persists the transient all_day flag normalizers compute (see
    -- asu_event_normalizer.is_explicit_all_day) so it survives while a
    -- candidate sits pending - never inferred from start_time IS NULL.
    all_day INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1)),
    review_reason TEXT,
    review_signals TEXT,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'dismissed')),
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- V0.10A multi-user foundation. events stays shared across all users;
-- per-user state lives here instead. No login/register UI yet - see
-- db.py for the bootstrap-owner migration that seeds this table from the
-- legacy events.status column for the current (single) installation.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    password_hash TEXT,
    -- V0.10C: bootstrap owner => 1, every other account defaults to 0.
    -- Never inferred from email/numeric id at authorization time - only
    -- the one-time db.py migration for the pre-existing bootstrap owner,
    -- and get_or_create_bootstrap_owner() itself, are allowed to set this
    -- by matching the known bootstrap owner email.
    is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- V0.10C: case-insensitive email identity. A declarative expression index
-- fails loudly (UNIQUE constraint violation) if an existing database
-- already has two rows differing only by case/whitespace - that's the
-- desired behavior (fail clearly, never silently merge accounts).
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (lower(email));

-- Per-user event status. events.status is legacy single-user state, kept
-- temporarily for compatibility; this table is the future source of
-- truth once the app switches over. Shared events are never duplicated
-- per user - only this join row is.
CREATE TABLE IF NOT EXISTS user_event_status (
    user_id INTEGER NOT NULL REFERENCES users(id),
    event_id INTEGER NOT NULL REFERENCES events(id),
    status TEXT NOT NULL CHECK (status IN ('new', 'interested', 'watching', 'ignored')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, event_id)
);

-- Opaque per-user tokens for future private ICS subscription URLs.
-- No route exposes these yet (V0.10A is foundation only).
CREATE TABLE IF NOT EXISTS calendar_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT
);

-- V0.10B: generic one-time-migration marker, so a data migration (like the
-- legacy events.status -> user_event_status backfill) can tell "already
-- applied" apart from "nothing to do yet" - not by guessing by table
-- row counts, which can't distinguish a genuinely empty table from an
-- interrupted migration.
CREATE TABLE IF NOT EXISTS app_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
