"""Pure source-health classifier: turns an event's existing bookkeeping
fields (consecutive_failures, last_check_status, event_state) into a
human-readable classification for the dashboard. No DB writes, no mutation,
and never decides to change event_state itself - that's an explicit user
action (see proposal_resolver/app.py for the actual mutation).
"""

REVIEW_THRESHOLD = 3


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def classify_source_health(event):
    """Classify `event`'s source health.

    Returns {"level", "needs_review", "consecutive_failures",
    "last_check_status", "message"}.

    level is one of: inactive, healthy, warning, repeated_failure, review.
    An inactive event is reported as "inactive" regardless of its failure
    count - it should never be presented as an active source-review warning.
    needs_review is true only at consecutive_failures >= 3.
    """
    if _get(event, "event_state", "active") == "inactive":
        return {
            "level": "inactive",
            "needs_review": False,
            "consecutive_failures": _get(event, "consecutive_failures", 0) or 0,
            "last_check_status": _get(event, "last_check_status"),
            "message": "Event is marked inactive.",
        }

    consecutive_failures = _get(event, "consecutive_failures", 0) or 0
    last_check_status = _get(event, "last_check_status")

    if consecutive_failures <= 0:
        level, message = "healthy", "Source is healthy."
    elif consecutive_failures == 1:
        level = "warning"
        message = f"Recent source issue ({last_check_status or 'unknown'})."
    elif consecutive_failures == 2:
        level = "repeated_failure"
        message = f"Repeated source issue ({last_check_status or 'unknown'})."
    else:
        level = "review"
        message = f"Source review recommended after {consecutive_failures} consecutive failures."

    return {
        "level": level,
        "needs_review": consecutive_failures >= REVIEW_THRESHOLD,
        "consecutive_failures": consecutive_failures,
        "last_check_status": last_check_status,
        "message": message,
    }
