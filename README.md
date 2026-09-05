# Event Radar (V0)

Small Flask app for tracking events and deciding whether they're worth adding
to your calendar. SQLite storage, server-rendered dashboard, no JS framework.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python seed.py      # creates data/events.db and loads sample events
python app.py        # runs on http://0.0.0.0:5000
```

## Usage

- Dashboard at `/` lists all events with three actions: Interested, Watching,
  Ignore.
- `/calendar.ics` returns an ICS feed containing only events marked
  Interested. Point your calendar app's "subscribe by URL" at this endpoint,
  or download it manually.

## Not in V0

No user accounts, no event collectors/scraping, no scheduling, no
deployment/Cloudflare config. Single SQLite file, single shared status per
event.
