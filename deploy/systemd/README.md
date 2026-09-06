# Event Radar - systemd --user units (V0.9A + V0.9B)

Two things run under `systemd --user` here:

1. **Maintenance timers** (V0.9A) - scheduled, unattended execution of the
   Event Radar CLI discovery/check scripts.
2. **The web dashboard** (V0.9B) - the Flask app served persistently via
   gunicorn, instead of manually running `python app.py`.

No Celery/Redis/APScheduler/cron/Docker/nginx - this machine runs
continuously, so plain systemd gives us scheduling, reboot persistence,
process supervision, and logs (journald) for free.

Neither part accepts/dismisses a review candidate or marks anything
Interested automatically - the resolver functions the dashboard/CLI call
already guarantee that (see their own docstrings); systemd just runs/serves
the existing code.

## Verified environment

- Repo: `/home/kanukuntla-r/workspace/event-radar`
- Python interpreter: `/home/kanukuntla-r/workspace/event-radar/.venv/bin/python` (symlink to system Python 3.14.6)
- `flock`: `/usr/bin/flock` (util-linux)
- `db.py` resolves `data/events.db` via `Path(__file__).parent` - already
  absolute and independent of the process's current working directory.
  `WorkingDirectory=` is still set in every unit as a matter of good
  practice, not because it's required for correctness.
- None of the four CLI scripts read an environment variable, need an
  interactive terminal, or import Flask - confirmed by inspection and by
  running each with the exact absolute interpreter/script paths from an
  unrelated working directory (`/tmp`) before writing any unit file.

## Units

| Unit | Runs | Schedule |
|---|---|---|
| `event-radar-hackathons.{service,timer}` | `run_hackathon_checks.py` | daily 07:00 |
| `event-radar-asu-career.{service,timer}` | `run_asu_career_discovery.py` | 00:15, 06:15, 12:15, 18:15 |
| `event-radar-asu-events.{service,timer}` | `run_asu_events_discovery.py` | 00:45, 06:45, 12:45, 18:45 |
| `event-radar-source-recovery.{service,timer}` | `run_source_recovery.py` | daily 07:30 |
| `event-radar-web.service` | Flask dashboard (via gunicorn) | always running, no timer |

All four timers have `Persistent=true` (a missed run while the machine was
off runs once at the next boot/daemon start) and `RandomizedDelaySec=600`
(a random 0-10 minute jitter so the ASU collectors don't hit the real sites
at an exact clock boundary every time).

The two ASU collectors are deliberately staggered by 30 minutes (`:15` vs
`:45`) so they never share a base minute, on top of the shared lock below.
Source recovery runs 30 minutes after the daily hackathon check, since
recovery eligibility (`consecutive_failures >= 3`) depends on that day's
check having already run.

Every service is `Type=oneshot` - it runs once to completion and exits;
nothing forks or backgrounds itself, and none of them start Flask.

## Overlap protection: one shared flock

Every `ExecStart=` wraps the Python invocation in:

```
/usr/bin/flock -n %t/event-radar-maintenance.lock <python> <script>
```

`%t` is systemd's runtime-directory specifier - `$XDG_RUNTIME_DIR` for a
user manager, e.g. `/run/user/1000`. That directory is tmpfs-backed,
per-user, and cleared on every boot, which is exactly the right lifetime
for a lock file: it must never be a stale leftover from a previous boot,
and it has no reason to live inside the git-tracked repo or persistent
`$HOME` state.

**All four services share the same lock file**, so at most one maintenance
script ever touches `data/events.db` at a time, regardless of which timer
fired. This is the actual overlap guard; the schedule staggering above is a
secondary courtesy to upstream sites, not the safety mechanism.

`-n` (`--nonblock`): if the lock is already held, `flock` fails immediately
(exit 1) instead of queuing up and waiting. **A skipped run due to the lock
counts as a failed service run**, visible in `systemctl --user status` and
`journalctl`. This was a deliberate choice, not an oversight: with the
schedule above, two of these jobs contending for the lock at the same
moment should be very rare (each run takes well under a minute, and the
closest two timers are 30 minutes apart) - if it ever happens, it's more
useful to see a failed unit (which might mean a previous run hung) than to
silently swallow a skip and have the DB simply not get checked that cycle.

## The web dashboard (`event-radar-web.service`)

Runs `gunicorn --bind 0.0.0.0:5000 --workers 1 --worker-class gthread --threads 2 --access-logfile - app:app`
from the repo directory. Unlike the maintenance units, this is `Type=simple`
(a long-running foreground process, not a one-shot) with `Restart=on-failure`
and `RestartSec=5`, and it has no timer - it's meant to just always be up.

**Server**: gunicorn was added (`pip install gunicorn` into `.venv`, and
added to `requirements.txt`) specifically because Flask's own dev server
(`app.run(debug=True)`, as `app.py`'s `if __name__ == "__main__":` block
still does for local manual use) auto-reloads on file changes, isn't
designed to be supervised as a single stable PID, and explicitly warns
against production use. Gunicorn is a small, conservative choice for this
app's actual load: 1 worker (no multi-process SQLite contention beyond what
the maintenance flock already serializes) with 2 threads (`gthread`) for
light concurrency - each request opens/closes its own `sqlite3` connection
(`db.py`'s `get_db()`), so per-thread connections are safe. Running
`app.py` directly still works for local dev; it's just not what
`event-radar-web.service` uses.

**Bind address**: `0.0.0.0:5000` - identical to what the previous
`python app.py` dev server already exposed (same host, same port). This is
a private-network-only decision: nothing here opens the port on the public
internet - that would require a router port-forward, which this phase does
not touch. Binding specifically to the Tailscale IP (`100.86.220.80`) was
considered and rejected: it would create a startup-ordering dependency on
`tailscale0` existing before gunicorn starts, which user-scope systemd
units can't reliably order against a system-level network interface coming
up. `0.0.0.0` avoids that race and keeps working on the LAN address too,
without changing the port or breaking any existing Apple Calendar
subscription URL (verified reachable on both `100.86.220.80:5000` and the
LAN address `192.168.0.181:5000` during validation).

**SQLite**: no configuration change was made. Connections are already
short-lived and per-request (`db.py`'s `get_db()` opens fresh, closes at
the end of the handler), the default `sqlite3.connect()` busy-timeout (5s)
comfortably covers the brief write window of a maintenance job, and the
maintenance jobs are already serialized against each other by the shared
flock. There was no demonstrated need for WAL mode or a custom timeout, so
none was added.

**From another Tailscale device** (e.g. your Mac): open
`http://100.86.220.80:5000/` in a browser, or subscribe Apple Calendar to
`http://100.86.220.80:5000/calendar.ics` (or whatever address/port the
existing subscription already uses - unchanged by this phase).

## Authentication deployment (V0.10C)

The web service now needs a persistent `EVENT_RADAR_SECRET_KEY` (Flask
session signing) supplied via an `EnvironmentFile=`, and the bootstrap
owner needs a real password before login can work at all.

**1. Generate the secret** (safe to run any time - never rotates an
existing secret, never prints the value):

```bash
deploy/generate-secret.sh
```

This creates `~/.config/event-radar/secret.env` (mode `0600`, directory
mode `0700`) containing `EVENT_RADAR_SECRET_KEY=<64 hex chars>`.

**2. Set the bootstrap owner's password** (interactive - never pass a
password as a CLI argument):

```bash
.venv/bin/python user_admin.py set-password --email owner@event-radar.local
```

**3. Install the updated unit and restart**:

```bash
deploy/install-user-units.sh
systemctl --user restart event-radar-web.service
```

Until steps 1-3 are all done, the owner's `password_hash` stays `NULL`
and login is impossible - restarting onto V0.10C code before setting a
password would lock out the dashboard (the legacy `/calendar.ics` feed
would keep working regardless, since it's intentionally unauthenticated
for this transition phase - see `app.py`'s `calendar_ics()`).

**Cookie security**: `SESSION_COOKIE_SECURE` is off by default (this
deployment is HTTP over trusted Tailscale/LAN - a Secure cookie simply
wouldn't be sent). Set `EVENT_RADAR_FORCE_SECURE_COOKIES=1` in
`secret.env` before ever deploying behind HTTPS/public Cloudflare.

## Repo-tracked source of truth

`deploy/systemd/*.service` and `*.timer` are the canonical, git-tracked
definitions. **Editing a file here does not change the installed unit** in
`~/.config/systemd/user/` - re-run the install script to sync:

```
deploy/install-user-units.sh
```

This copies the unit files (maintenance + the web service) and runs
`systemctl --user daemon-reload`. It never enables or starts anything -
enabling is always a separate, explicit command (see below). No `sudo`, no
`curl | bash`.

## Validation before enabling anything

```
systemd-analyze --user verify ~/.config/systemd/user/event-radar-*.service ~/.config/systemd/user/event-radar-*.timer
```

Then run each service manually once and inspect it before trusting a
timer with it:

```
systemctl --user start event-radar-hackathons.service
systemctl --user status event-radar-hackathons.service
journalctl --user -u event-radar-hackathons.service -n 100 --no-pager
```

Repeat for `event-radar-asu-career.service`, `event-radar-asu-events.service`,
`event-radar-source-recovery.service`.

For the web service, after starting it manually once, also check it over
HTTP before trusting it:

```
systemctl --user start event-radar-web.service
curl -I http://127.0.0.1:5000/
curl http://127.0.0.1:5000/calendar.ics
```

## Enabling (only after validation passes)

```
systemctl --user enable --now event-radar-hackathons.timer
systemctl --user enable --now event-radar-asu-career.timer
systemctl --user enable --now event-radar-asu-events.timer
systemctl --user enable --now event-radar-source-recovery.timer
systemctl --user enable --now event-radar-web.service
```

For the four maintenance jobs, only the `.timer` units are enabled - never
the `.service` units directly (a service isn't `WantedBy=` anything; it's
meant to be started by its timer or manually, not enabled itself).
`event-radar-web.service` is the one exception - it's enabled directly
(`WantedBy=default.target`) since it has no timer; "enabled" for it means
"start at login/boot and stay running," not "run on a schedule."

## Session persistence (linger)

```
loginctl show-user kanukuntla-r -p Linger
```

`Linger=yes` was already set on this machine before V0.9A - the user
systemd instance (and therefore these timers) keeps running across logout
and survives reboots without requiring an active login session, which is
exactly what an always-on home server needs. Nothing needed to change
here; if it had been `Linger=no`, timers would only run while at least one
session for this user is active, and `sudo loginctl enable-linger
kanukuntla-r` would be the fix - not done automatically by this phase.

## Operational commands

Maintenance jobs (substitute any of the four unit names):

```
# List all timers and their next/last run
systemctl --user list-timers --all

# Status of one service
systemctl --user status event-radar-asu-events.service

# Last 100 log lines for one unit
journalctl --user -u event-radar-asu-events.service -n 100 --no-pager

# Follow logs live
journalctl --user -u event-radar-asu-events.service -f

# Run a job right now, outside its schedule
systemctl --user start event-radar-asu-events.service

# Stop scheduling a job (leaves the .service file in place)
systemctl --user disable --now event-radar-asu-events.timer

# After editing a unit file directly in ~/.config/systemd/user/
systemctl --user daemon-reload
```

Dashboard (`event-radar-web.service`):

```
# Status
systemctl --user status event-radar-web.service

# Last 100 log lines
journalctl --user -u event-radar-web.service -n 100 --no-pager

# Follow logs live
journalctl --user -u event-radar-web.service -f

# Restart (e.g. after a code change)
systemctl --user restart event-radar-web.service

# Stop
systemctl --user stop event-radar-web.service

# Stop it from starting automatically again
systemctl --user disable --now event-radar-web.service
```

Gunicorn is given `--access-logfile -`, so per-request access lines are
written to stdout, which journald already captures. To see who hit which
route:

```bash
journalctl --user -u event-radar-web.service
```
