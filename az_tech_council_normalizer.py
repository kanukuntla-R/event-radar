"""Turns raw aztechcouncil.org (Arizona Technology Council) listing/detail
evidence into a normalized discovery candidate. Reuses asu_event_normalizer's
shared pure date/time/all-day/URL helpers rather than duplicating them -
those helpers are already site-agnostic (plain text in, ISO/HH:MM/bool out;
see asu_events_normalizer.py, which does the same for a different site).
Does NOT reuse asu_event_classifier.classify_asu_event - that's an ASU-only
event_subtype taxonomy with no AZ Tech Council equivalent, so event_subtype
is left None here rather than forced into a taxonomy that doesn't fit.
"""
import html
import re

from asu_event_normalizer import (
    parse_date_range, parse_time_range, is_explicit_all_day,
    normalize_url, ARIZONA_TIMEZONE,
)
from event_location import classify_location

# V0.12A: one stable canonical source name for every AZ Tech Council event,
# regardless of which category bucket (AZTC-branded vs. community-tech-
# events) or page it was discovered under.
SOURCE_NAME = "Arizona Technology Council"

# events.event_type is a coarse "source family" tag (existing values: 'asu',
# 'hackathon'). AZ Tech Council is neither - it's a broad professional/
# networking/tech-community events source, so it gets its own tag rather
# than silently defaulting to event_discovery_store.DEFAULTS['event_type']
# ('asu' - a leftover default from when only ASU sources called that store).
EVENT_TYPE = "professional"


def _clean_text(text):
    if not text:
        return None
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_candidate(raw):
    """Turn one raw AZ Tech Council listing/detail hit into a normalized
    discovery candidate dict, ready for event_dedup/event_discovery_store.

    `raw` may contain: name, description, date_text, time_text,
    location_text, source_url, registration_url, organizer, categories
    (list[str] of event_category taxonomy slugs). Any missing/unparseable
    piece stays None rather than being invented - same invariant as every
    other source here: `all_day` is only true when the source text
    explicitly says "All Day"/"All-Day" - a missing/unparseable time never
    silently becomes all-day.
    """
    name = _clean_text(raw.get("name"))
    description = _clean_text(raw.get("description"))

    time_text = raw.get("time_text")
    start_date, end_date = parse_date_range(raw.get("date_text"))
    start_time, end_time = parse_time_range(time_text)
    all_day = is_explicit_all_day(time_text)

    location_text = _clean_text(raw.get("location_text"))
    location_info = classify_location(location_text)

    source_url = normalize_url(raw.get("source_url"))
    registration_url = normalize_url(raw.get("registration_url"))
    # "registration URL if distinct" (V0.12A spec) - a Register button that
    # just links back to this same event page carries no new information,
    # so it's dropped rather than duplicating source_url into a second field.
    if registration_url and registration_url == source_url:
        registration_url = None
    registration_status = "open" if registration_url else "unknown"

    categories = [c for c in (_clean_text(c) for c in (raw.get("categories") or [])) if c]

    return {
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
        "event_type": EVENT_TYPE,
        "event_subtype": None,
        "source": SOURCE_NAME,
        "source_url": source_url,
        "organizer": _clean_text(raw.get("organizer")) or SOURCE_NAME,
        "registration_status": registration_status,
        "registration_url": registration_url,
        "application_deadline": None,
        "all_day": all_day,
        "categories": categories,
    }
