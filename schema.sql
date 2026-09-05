-- registration_status: unknown | not_open | open | closed (no DB constraint yet -
-- nothing writes this field through the app, so it's just documented here)
-- travel_required: 0 = local/remote, 1 = travel needed
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
