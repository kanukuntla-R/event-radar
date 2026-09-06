"""Turns raw ASU Career Services listing/detail evidence into a normalized
discovery candidate. Parsing/classification only - no DB writes, no HTTP.
Never fabricates a date/time it can't parse; missing evidence stays None.
"""
import html
import re
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from event_location import classify_location
from asu_event_classifier import classify_asu_event

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_RE = re.compile(rf"\b({MONTH_PATTERN})\s+(\d{{1,2}}),\s*(\d{{4}})\b", re.IGNORECASE)
TIME_RE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:-|–|—)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)
ALL_DAY_KEYWORDS = ("all day", "all-day")

ARIZONA_TIMEZONE = "America/Phoenix"
SOURCE_NAME = "ASU Career Services"


def normalize_url(url):
    """Lower-case scheme/host and strip a trailing path slash, but keep the
    query string and fragment intact - unlike source_recovery.normalize_url,
    which drops fragments for dedup comparison. Several real registration
    links here (12twenty) are single-page-app routes where the fragment
    (`#/events/12345`) is the only thing identifying the specific event;
    dropping it would silently collapse every event onto the same generic
    URL. Returns None for falsy input.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


def _clean_text(text):
    if not text:
        return None
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _parse_date_component(text):
    match = DATE_RE.search(text)
    if not match:
        return None
    month_name, day, year = match.groups()
    month = MONTHS[month_name.lower()]
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def parse_date_range(date_text):
    """"Weekday, Month D, YYYY[ - Weekday, Month D, YYYY]" -> (start, end)
    ISO date strings. Returns (None, None) if unparseable - never invents a
    date.
    """
    if not date_text:
        return None, None
    date_text = html.unescape(date_text)
    parts = re.split(r"\s+(?:-|–|—)\s+", date_text, maxsplit=1)
    start = _parse_date_component(parts[0])
    end = _parse_date_component(parts[1]) if len(parts) > 1 else start
    return start, end


def _to_24h(hour, minute, ampm):
    hour = int(hour)
    minute = int(minute) if minute else 0
    ampm = ampm.lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def parse_time_range(time_text):
    """"Ham[:MM] - Hpm[:MM][ MST]" -> (start "HH:MM", end "HH:MM"). Returns
    (None, None) if unparseable - never invents a time.
    """
    if not time_text:
        return None, None
    time_text = html.unescape(time_text)
    match = TIME_RE.search(time_text)
    if not match:
        return None, None
    start_h, start_m, start_ap, end_h, end_m, end_ap = match.groups()
    return _to_24h(start_h, start_m, start_ap), _to_24h(end_h, end_m, end_ap)


def is_explicit_all_day(time_text):
    """True only when the source text itself says "All Day"/"All-Day".
    Missing time_text or unparseable time_text is NOT all-day evidence -
    it's just missing/bad data, handled as an incomplete candidate instead.
    """
    if not time_text:
        return False
    return any(kw in html.unescape(time_text).strip().lower() for kw in ALL_DAY_KEYWORDS)


# ponytail: ASU Career Services (uConnect) represents some recurring/
# multi-occurrence events (e.g. weekly drop-in hours, a recurring virtual
# info session) with one listing "Date: first - last" range plus a single
# daily time window, and exposes NO structured recurrence data anywhere -
# confirmed against the source's OWN "Add to Calendar" .ics export, which
# reproduces the exact same multi-week/month span with no RRULE. That's
# upstream data, not something we can recover the real occurrence dates
# from. Treating the far date as a continuous event's end produces an
# absurd multi-month calendar entry. Every genuine multi-day timed event
# actually seen in this source so far spans 1 day; this threshold gives
# real multi-day events room (up to a week) while catching every
# confirmed recurring-series case (23-126 days). Revisit if a genuine
# longer continuous timed event is ever found.
MAX_TIMED_EVENT_SPAN_DAYS = 7

RECURRING_SERIES_NOTE = (
    "Source lists this as a recurring/multi-occurrence series through {end_date}; "
    "only the first occurrence date is tracked."
)


def resolve_timed_event_date_range(start_date, end_date, start_time, description):
    """ASU Career Services (career.eoss.asu.edu / uConnect) ONLY - see the
    MAX_TIMED_EVENT_SPAN_DAYS comment above for why. Do not call this from
    a different source's normalizer without independently proving that
    source collapses recurring series into one date range the same way;
    asu_events_normalizer.py (asuevents.asu.edu / Drupal) deliberately does
    NOT use this, since a legitimate broader ASU Events timed program can
    genuinely span more than a week and must keep its real end_date.

    Decide whether `end_date` is trustworthy as the close of one
    continuous TIMED event, or is actually the tail of a recurring series
    that must not be encoded as a single multi-week/month VEVENT.

    Only applies when a start_time is present (timed event) and an
    end_date differs from start_date - an all-day date range is a
    different semantic, out of scope here. Never invents a date: past the
    threshold it returns `end_date=None` (falls back to the first
    occurrence only, same as an event that never had an end_date) plus an
    appended note in `description` recording the discarded evidence for
    future improvement, rather than silently dropping it.

    Returns (end_date, description).
    """
    if not (start_time and end_date and start_date and end_date != start_date):
        return end_date, description
    try:
        span_days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
    except ValueError:
        return end_date, description
    if span_days <= MAX_TIMED_EVENT_SPAN_DAYS:
        return end_date, description

    note = RECURRING_SERIES_NOTE.format(end_date=end_date)
    new_description = f"{description} {note}" if description else note
    return None, new_description


def normalize_candidate(raw):
    """Turn one raw ASU Career Services listing/detail hit into a normalized
    discovery candidate dict, ready for event_dedup/event_discovery_store.

    `raw` may contain: name, description, date_text, time_text,
    location_text, source_url, registration_url. Any missing/unparseable
    piece stays None rather than being invented - e.g. a start_time that
    doesn't parse leaves start_time/end_time/timezone all None. Unlike a
    hackathon's TBA-time event, an ASU listing almost always states a time,
    so a missing start_time here is NOT assumed to mean "all-day": the
    `all_day` field is only true when the source text explicitly says
    "All Day"/"All-Day". event_discovery_store.validate_candidate() uses
    that flag to reject a timeless candidate that isn't confirmed all-day,
    rather than let it silently become a false all-day calendar entry.
    """
    name = _clean_text(raw.get("name"))
    description = _clean_text(raw.get("description"))

    time_text = raw.get("time_text")
    start_date, end_date = parse_date_range(raw.get("date_text"))
    start_time, end_time = parse_time_range(time_text)
    all_day = is_explicit_all_day(time_text)
    end_date, description = resolve_timed_event_date_range(start_date, end_date, start_time, description)

    location_text = _clean_text(raw.get("location_text"))
    location_info = classify_location(location_text)

    registration_url = normalize_url(raw.get("registration_url"))
    registration_status = "open" if registration_url else "unknown"

    candidate = {
        "name": name,
        "description": description,
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "timezone": ARIZONA_TIMEZONE if start_time else None,
        "date_status": "confirmed" if start_date else None,
        "location": location_text,
        "location_type": location_info["location_type"],
        "location_area": location_info["location_area"],
        "source": SOURCE_NAME,
        "source_url": normalize_url(raw.get("source_url")),
        "organizer": SOURCE_NAME,
        "registration_status": registration_status,
        "registration_url": registration_url,
        "application_deadline": None,
        "all_day": all_day,
    }
    candidate["event_subtype"] = classify_asu_event(candidate)
    return candidate
