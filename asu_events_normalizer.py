"""Turns raw asuevents.asu.edu listing/detail evidence into a normalized
discovery candidate. Independent from asu_event_normalizer.py's site (a
different HTML source), but reuses its shared pure date/time/all-day/URL
helpers rather than duplicating them - those helpers are already
site-agnostic (plain text in, ISO/HH:MM/bool out).
"""
from asu_event_normalizer import (
    parse_date_range, parse_time_range, is_explicit_all_day,
    normalize_url, ARIZONA_TIMEZONE,
)
from event_location import classify_location
from asu_event_classifier import classify_asu_event

import html
import re

SOURCE_NAME = "ASU Events"


def _clean_text(text):
    if not text:
        return None
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_candidate(raw):
    """Turn one raw ASU Events listing/detail hit into a normalized
    discovery candidate dict, ready for event_dedup/event_discovery_store.

    `raw` may contain: name, description, date_text, time_text,
    location_text, source_url, registration_url, categories (list[str]),
    audiences (list[str]), organizer. Any missing/unparseable piece stays
    None rather than being invented, same invariant as ASU Career Services:
    `all_day` is only true when the source text explicitly says "All Day"/
    "All-Day" - a missing/unparseable time never silently becomes all-day.
    """
    name = _clean_text(raw.get("name"))
    description = _clean_text(raw.get("description"))

    time_text = raw.get("time_text")
    start_date, end_date = parse_date_range(raw.get("date_text"))
    start_time, end_time = parse_time_range(time_text)
    all_day = is_explicit_all_day(time_text)
    # Deliberately NOT applying asu_event_normalizer.resolve_timed_event_date_range
    # here: the "first date - last date + one daily time window means a
    # recurring series" pattern is only proven for ASU Career Services
    # (career.eoss.asu.edu / uConnect - confirmed against that platform's
    # own .ics export). A broader ASU Events (asuevents.asu.edu / Drupal)
    # timed program legitimately spanning 8+ days must keep its real
    # end_date unless this source independently provides its own
    # recurrence evidence.

    location_text = _clean_text(raw.get("location_text"))
    location_info = classify_location(location_text)

    registration_url = normalize_url(raw.get("registration_url"))
    registration_status = "open" if registration_url else "unknown"

    categories = [c for c in (_clean_text(c) for c in (raw.get("categories") or [])) if c]
    audiences = [a for a in (_clean_text(a) for a in (raw.get("audiences") or [])) if a]

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
        "organizer": _clean_text(raw.get("organizer")) or SOURCE_NAME,
        "registration_status": registration_status,
        "registration_url": registration_url,
        "application_deadline": None,
        "all_day": all_day,
        "categories": categories,
        "audiences": audiences,
    }
    candidate["event_subtype"] = classify_asu_event(candidate)
    return candidate
