"""Read-only hackathon source collector.

Fetches an event's official source_url and returns a structured
CollectorResult describing what was found. This module never touches
SQLite and never mutates the event passed in - it only reports.

Deliberately out of scope for this phase (belongs to a later change-detector
step): event_state, inactive_reason, consecutive_failures, last_checked,
last_successful_check are never set here.
"""
import re
import socket
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

USER_AGENT = "EventRadar/0.3 (personal hackathon watchlist; read-only checker)"
DEFAULT_TIMEOUT = 10

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_NAME_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))

# ponytail: keyword lists cover common phrasing seen on hackathon sites.
# Anything not matching one of these stays None rather than guessing -
# extend the lists if a real page uses different wording.
REGISTRATION_CLOSED_PHRASES = (
    "registration is closed", "registration closed",
    "applications closed", "applications are closed",
)
REGISTRATION_OPEN_PHRASES = (
    "apply now", "register now", "registration is open", "registration open",
    "applications are open", "applications open now", "apply today",
)
REGISTRATION_NOT_OPEN_PHRASES = (
    "registration opens soon", "applications opening soon", "applications open soon",
    "registration coming soon", "coming soon", "stay tuned",
)
APPLY_LINK_TEXT_KEYWORDS = ("apply", "register")

TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
LINK_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
CHARSET_RE = re.compile(r"charset=([\w-]+)", re.IGNORECASE)

ISO_DATE_RANGE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|–|—)\s*(\d{4}-\d{2}-\d{2})"
)
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
MONTH_DAY_RANGE_RE = re.compile(
    rf"\b({MONTH_NAME_PATTERN})\s+(\d{{1,2}})\s*(?:-|–|—|to)\s*(\d{{1,2}}),?\s+(\d{{4}})\b",
    re.IGNORECASE,
)
MONTH_DAY_SINGLE_RE = re.compile(
    rf"\b({MONTH_NAME_PATTERN})\s+(\d{{1,2}}),?\s+(\d{{4}})\b",
    re.IGNORECASE,
)


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _strip_tags(html_text):
    without_scripts = SCRIPT_STYLE_RE.sub(" ", html_text)
    text = TAG_RE.sub(" ", without_scripts)
    return re.sub(r"\s+", " ", text).strip()


def _extract_title(html_text):
    match = TITLE_RE.search(html_text)
    if not match:
        return None
    title = _strip_tags(match.group(1)).strip()
    return title or None


def _detect_registration_status(text):
    lowered = text.lower()
    for phrase in REGISTRATION_CLOSED_PHRASES:
        if phrase in lowered:
            return "closed", phrase
    for phrase in REGISTRATION_OPEN_PHRASES:
        if phrase in lowered:
            return "open", phrase
    for phrase in REGISTRATION_NOT_OPEN_PHRASES:
        if phrase in lowered:
            return "not_open", phrase
    return None, None


def _detect_registration_url(html_text, base_url):
    for href, link_text in LINK_RE.findall(html_text):
        visible = _strip_tags(link_text).lower()
        if any(keyword in visible for keyword in APPLY_LINK_TEXT_KEYWORDS):
            return urljoin(base_url, href), visible
    return None, None


def _month_day_year_to_iso(month_name, day, year):
    month = MONTHS[month_name.lower()]
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def _detect_date_range(text):
    match = ISO_DATE_RANGE_RE.search(text)
    if match:
        return match.group(1), match.group(2), match.group(0)

    match = MONTH_DAY_RANGE_RE.search(text)
    if match:
        month_name, start_day, end_day, year = match.groups()
        start_iso = _month_day_year_to_iso(month_name, start_day, year)
        end_iso = _month_day_year_to_iso(month_name, end_day, year)
        return start_iso, end_iso, match.group(0)

    match = MONTH_DAY_SINGLE_RE.search(text)
    if match:
        month_name, day, year = match.groups()
        iso = _month_day_year_to_iso(month_name, day, year)
        return iso, iso, match.group(0)

    return None, None, None


def _detect_application_deadline(text):
    for occurrence in re.finditer(r"deadline", text, re.IGNORECASE):
        window = text[occurrence.start(): occurrence.start() + 100]
        iso_match = ISO_DATE_RE.search(window)
        if iso_match:
            return iso_match.group(0), window.strip()
        month_match = MONTH_DAY_SINGLE_RE.search(window)
        if month_match:
            month_name, day, year = month_match.groups()
            return _month_day_year_to_iso(month_name, day, year), window.strip()
    return None, None


def _empty_detected():
    return {
        "start_date": None,
        "end_date": None,
        "date_status": None,
        "registration_status": None,
        "registration_url": None,
        "application_deadline": None,
    }


def _result(source_result, requested_url, checked_at, final_url=None, http_status=None,
            page_title=None, raw_evidence=None, detected=None, notes=None):
    return {
        "source_result": source_result,
        "requested_url": requested_url,
        "final_url": final_url,
        "http_status": http_status,
        "checked_at": checked_at,
        "page_title": page_title,
        "raw_evidence": raw_evidence or {},
        "detected": detected or _empty_detected(),
        "notes": notes or [],
    }


def collect(event, timeout=DEFAULT_TIMEOUT, now=None):
    """Fetch `event`'s source_url and report what was found. Read-only.

    `event` (dict or sqlite3.Row) is never mutated. Returns a CollectorResult
    dict - see module docstring for the fields this deliberately never sets.
    """
    checked_at = (now or datetime.now(timezone.utc)).isoformat()
    requested_url = _get(event, "source_url")

    if not requested_url:
        return _result(
            "unavailable", requested_url, checked_at,
            notes=["Event has no source_url to check."],
        )

    request = Request(requested_url, headers={"User-Agent": USER_AGENT})

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            final_url = response.geturl()
            http_status = getattr(response, "status", None) or response.getcode()
            content_type = response.headers.get("Content-Type", "") if response.headers else ""
    except HTTPError as e:
        source_result = "not_found" if e.code == 404 else "unavailable"
        return _result(
            source_result, requested_url, checked_at,
            final_url=e.geturl() if hasattr(e, "geturl") else None,
            http_status=e.code,
            notes=[f"HTTP error {e.code}: {e.reason}"],
        )
    except (URLError, socket.timeout, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        return _result(
            "unavailable", requested_url, checked_at,
            notes=[f"Connection failed: {reason}"],
        )
    except Exception as e:  # pragma: no cover - defensive catch-all
        return _result(
            "unavailable", requested_url, checked_at,
            notes=[f"Unexpected error: {e!r}"],
        )

    charset_match = CHARSET_RE.search(content_type)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        html_text = body.decode(charset, errors="replace")
    except LookupError:
        html_text = body.decode("utf-8", errors="replace")

    title = _extract_title(html_text)
    text = _strip_tags(html_text)

    if not html_text.strip() or (not title and len(text) < 20):
        return _result(
            "parse_failed", requested_url, checked_at,
            final_url=final_url, http_status=http_status,
            raw_evidence={"content_type": content_type, "content_length": len(body)},
            notes=["Response body could not be interpreted as a usable page."],
        )

    registration_status, registration_phrase = _detect_registration_status(text)
    registration_url, link_text = _detect_registration_url(html_text, final_url)
    start_date, end_date, date_match_text = _detect_date_range(text)
    deadline, deadline_match_text = _detect_application_deadline(text)

    notes = []
    notes.append(f"Detected registration_status via phrase: {registration_phrase!r}." if registration_phrase
                  else "No registration-status wording recognized.")
    notes.append(f"Detected registration link text: {link_text!r}." if link_text
                 else "No apply/register link recognized.")
    notes.append(f"Detected date range via text: {date_match_text!r}." if date_match_text
                 else "No confirmed date pattern recognized.")
    notes.append(f"Detected application deadline via text near: {deadline_match_text!r}." if deadline_match_text
                 else "No application deadline pattern recognized.")

    source_result = "redirected" if final_url != requested_url else "ok"

    return _result(
        source_result, requested_url, checked_at,
        final_url=final_url, http_status=http_status, page_title=title,
        raw_evidence={
            "content_type": content_type,
            "content_length": len(body),
            "registration_phrase": registration_phrase,
            "registration_link_text": link_text,
            "date_match_text": date_match_text,
            "deadline_match_text": deadline_match_text,
        },
        detected={
            "start_date": start_date,
            "end_date": end_date,
            "date_status": "confirmed" if start_date else None,
            "registration_status": registration_status,
            "registration_url": registration_url,
            "application_deadline": deadline,
        },
        notes=notes,
    )
