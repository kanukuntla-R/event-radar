from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRODID = "-//Event Radar//V0//EN"

# ponytail: home base default when a timed event has no explicit timezone.
# Non-Arizona events (travel, remote conferences) should always set their own
# timezone field - this fallback only covers sloppy/missing data.
DEFAULT_TIMEZONE = "America/Phoenix"

# ponytail: fallback event length when start_time is set but end_time isn't.
DEFAULT_DURATION = timedelta(hours=1)


def _escape(text):
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _resolve_zone(tz_name):
    try:
        return ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _timed_vevent_lines(e):
    """DTSTART/DTEND for an event with a start_time, converted to UTC instants."""
    tz = _resolve_zone(e["timezone"])
    start_dt = datetime.strptime(f"{e['start_date']} {e['start_time']}", "%Y-%m-%d %H:%M")
    start_dt = start_dt.replace(tzinfo=tz)

    if e["end_time"]:
        end_date_str = e["end_date"] or e["start_date"]
        end_dt = datetime.strptime(f"{end_date_str} {e['end_time']}", "%Y-%m-%d %H:%M")
        end_dt = end_dt.replace(tzinfo=tz)
    else:
        end_dt = start_dt + DEFAULT_DURATION

    start_utc = start_dt.astimezone(dt_timezone.utc)
    end_utc = end_dt.astimezone(dt_timezone.utc)
    return [
        f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
    ]


def _all_day_vevent_lines(e):
    start_dt = datetime.strptime(e["start_date"], "%Y-%m-%d")
    try:
        end_dt = datetime.strptime(e["end_date"], "%Y-%m-%d") if e["end_date"] else start_dt
    except ValueError:
        end_dt = start_dt
    end_dt_exclusive = end_dt + timedelta(days=1)  # DTEND is exclusive for all-day events
    return [
        f"DTSTART;VALUE=DATE:{start_dt.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{end_dt_exclusive.strftime('%Y%m%d')}",
    ]


def generate_ics(events):
    """Build an ICS calendar from a list of event rows.

    Events with a start_time become timed VEVENTs (converted to UTC via the
    event's timezone, or DEFAULT_TIMEZONE if unset). Events without a
    start_time stay all-day VEVENTs, exactly as before. Events with no
    parseable start_date are skipped entirely - there's nothing to put on a
    calendar for a date that's still TBA.
    """
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN"]
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    for e in events:
        if not e["start_date"]:
            continue
        try:
            datetime.strptime(e["start_date"], "%Y-%m-%d")
        except ValueError:
            continue

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:event-{e['id']}@event-radar")
        lines.append(f"DTSTAMP:{now}")
        if e["start_time"]:
            lines.extend(_timed_vevent_lines(e))
        else:
            lines.extend(_all_day_vevent_lines(e))
        lines.append(f"SUMMARY:{_escape(e['name'])}")
        if e["location"]:
            lines.append(f"LOCATION:{_escape(e['location'])}")
        if e["description"]:
            lines.append(f"DESCRIPTION:{_escape(e['description'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
