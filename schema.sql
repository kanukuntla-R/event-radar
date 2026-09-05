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
