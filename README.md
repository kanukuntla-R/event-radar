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
- `/calendar` (after logging in) shows your private calendar subscription
  URL, an ICS feed containing only your own Interested events. Point your
  calendar app's "subscribe by URL" at that private URL.

## Not in V0

No user accounts, no event collectors/scraping, no scheduling, no
deployment/Cloudflare config. Single SQLite file, single shared status per
event.
