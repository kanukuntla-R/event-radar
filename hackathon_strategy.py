"""Pure decision strategy: should a hackathon listing be re-checked today?

No I/O: no network calls, no SQLite writes, no Flask. `evaluate()` reads
`event` (a dict or sqlite3.Row) and never mutates it.
"""
from datetime import date, datetime, timedelta

REGISTRATION_CADENCE_DAYS = {
    "unknown": 7,
    "not_open": 7,
    "open": 7,
    "closed": 14,
}

TBA_DEFAULT_CADENCE_DAYS = 14  # no expected_start_date to go on
TBA_FAR_CADENCE_DAYS = 30      # > 6 months away
TBA_MID_CADENCE_DAYS = 14      # 3-6 months away
TBA_NEAR_CADENCE_DAYS = 7      # < 3 months away
TBA_FAR_THRESHOLD_DAYS = 180   # ~6 months
TBA_NEAR_THRESHOLD_DAYS = 90   # ~3 months


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _parse_date(value):
    """Accepts None, date, datetime, or a SQLite date/datetime string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.split(" ")[0].split("T")[0]  # drop any time component
    return date.fromisoformat(text)


def _descriptor(date_status, registration_status):
    if date_status == "tba":
        return "date_tba"
    if date_status == "confirmed":
        reg = registration_status if registration_status in REGISTRATION_CADENCE_DAYS else "unknown"
        return f"registration_{reg}"
    return None  # ponytail: schema CHECK guarantees confirmed/tba; defensive only


def _tba_cadence_days(expected_start_date, today):
    if expected_start_date is None:
        return TBA_DEFAULT_CADENCE_DAYS
    days_away = (expected_start_date - today).days
    if days_away > TBA_FAR_THRESHOLD_DAYS:
        return TBA_FAR_CADENCE_DAYS
    if days_away >= TBA_NEAR_THRESHOLD_DAYS:
        return TBA_MID_CADENCE_DAYS
    return TBA_NEAR_CADENCE_DAYS


def _cadence_days(date_status, registration_status, expected_start_date, today):
    if date_status == "tba":
        return _tba_cadence_days(expected_start_date, today)
    reg = registration_status if registration_status in REGISTRATION_CADENCE_DAYS else "unknown"
    return REGISTRATION_CADENCE_DAYS[reg]


def evaluate(event, today=None):
    """Decide whether `event` should be checked today.

    Returns {"should_check": bool, "reason": str, "next_check": date|None}.
    """
    today = today or date.today()

    date_status = _get(event, "date_status")
    registration_status = _get(event, "registration_status")
    start_date = _parse_date(_get(event, "start_date"))
    end_date = _parse_date(_get(event, "end_date"))
    last_checked = _parse_date(_get(event, "last_checked"))
    expected_start_date = _parse_date(_get(event, "expected_start_date"))

    effective_end = end_date or start_date  # single-day confirmed events may only set start_date
    if date_status == "confirmed" and effective_end is not None and effective_end < today:
        return {"should_check": False, "reason": "event_finished", "next_check": None}

    descriptor = _descriptor(date_status, registration_status)

    if last_checked is None:
        reason = f"{descriptor}_never_checked" if descriptor else "never_checked"
        return {"should_check": True, "reason": reason, "next_check": None}

    cadence_days = _cadence_days(date_status, registration_status, expected_start_date, today)
    elapsed = (today - last_checked).days

    if elapsed >= cadence_days:
        if date_status == "tba":
            due_reason = "tba_check_due"
        elif descriptor:
            due_reason = f"{descriptor}_check_due"
        else:
            due_reason = "check_due"
        return {"should_check": True, "reason": due_reason, "next_check": None}

    next_check = last_checked + timedelta(days=cadence_days)
    return {"should_check": False, "reason": "checked_recently", "next_check": next_check}
