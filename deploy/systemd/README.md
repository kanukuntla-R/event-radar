# Event Radar - systemd --user maintenance timers (V0.9A)

Scheduled, unattended execution of the existing Event Radar CLI maintenance
scripts via `systemd --user` services + timers. No Celery/Redis/APScheduler/
cron/Docker - this machine runs continuously, so plain systemd gives us
scheduling, reboot persistence, process supervision, and logs (journald)
for free.

This phase is scheduling only. It does not deploy the Flask dashboard, and
it never accepts/dismisses a review candidate or marks anything Interested
automatically - the underlying scripts already guarantee that (see their own
docstrings); the timers just run them on a schedule.

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

## Repo-tracked source of truth

`deploy/systemd/*.service` and `*.timer` are the canonical, git-tracked
definitions. **Editing a file here does not change the installed unit** in
`~/.config/systemd/user/` - re-run the install script to sync:

```
deploy/install-user-timers.sh
```

This copies the unit files and runs `systemctl --user daemon-reload`. It
never enables or starts anything - enabling is always a separate, explicit
command (see below). No `sudo`, no `curl | bash`.

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

## Enabling timers (only after validation passes)

```
systemctl --user enable --now event-radar-hackathons.timer
systemctl --user enable --now event-radar-asu-career.timer
systemctl --user enable --now event-radar-asu-events.timer
systemctl --user enable --now event-radar-source-recovery.timer
```

Only the `.timer` units are enabled - never the `.service` units directly
(a service isn't `WantedBy=` anything; it's meant to be started by its
timer or manually, not enabled itself).

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
