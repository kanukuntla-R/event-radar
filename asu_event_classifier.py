"""Pure ASU Career Services event-subtype classifier. Deterministic
keyword/rule based only - no ML/LLM calls. Conservative: text that matches
no rule classifies as "other" rather than guessing.
"""

CAREER_FAIR_KEYWORDS = ("career fair", "internship fair", "job fair", "hiring fair")
EMPLOYER_EVENT_KEYWORDS = (
    "discovery day", "employer visit", "recruiter", "meet the employer",
    "employer info session", "company info session",
)
NETWORKING_KEYWORDS = ("networking", "mixer")
INFO_SESSION_KEYWORDS = ("info session", "information session", "get to know")
WORKSHOP_KEYWORDS = (
    "resume", "résumé", "interview", "job search", "career prep",
    "workshop", "cover letter",
)
CAREER_SERVICES_KEYWORDS = (
    "tabling", "career consulting", "advising", "drop-in", "drop in", "office hours",
)

# Precedence, most specific evidence first.
_RULES = (
    (CAREER_FAIR_KEYWORDS, "career_fair"),
    (EMPLOYER_EVENT_KEYWORDS, "employer_event"),
    (NETWORKING_KEYWORDS, "networking"),
    (INFO_SESSION_KEYWORDS, "info_session"),
    (WORKSHOP_KEYWORDS, "workshop"),
    (CAREER_SERVICES_KEYWORDS, "career_services"),
)


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def classify_asu_event(event):
    """Classify one ASU Career Services event into exactly one subtype,
    using name/description text evidence only. Deterministic keyword
    precedence: career_fair > employer_event > networking > info_session >
    workshop > career_services > other. No DB/HTTP access, no mutation.
    """
    text = " ".join(filter(None, [_get(event, "name"), _get(event, "description")])).lower()

    for keywords, subtype in _RULES:
        if any(kw in text for kw in keywords):
            return subtype
    return "other"
