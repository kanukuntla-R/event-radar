"""Pure ASU event-subtype classifier (shared by ASU Career Services and ASU
Events discovery). Deterministic keyword/rule based only - no ML/LLM calls.
Conservative: text that matches no rule classifies as "other" rather than
guessing.
"""
import re

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

# Added for V0.8B (ASU Events is a much broader source than ASU Career
# Services, where these never came up). Kept to exactly three additions,
# justified by real observed events (e.g. an NVIDIA/OpenUSD guest lecture,
# a research pitch competition) rather than speculative categories.
TECH_EVENT_PHRASES = (
    "nvidia", "artificial intelligence", "machine learning", "cybersecurity",
    "software", "cloud computing", "data science", "robotics", "coding",
    "hackathon", "developer", "computing", "omniverse", "engineering",
)
TECH_EVENT_WORD_TOKENS = ("ai", "ml", "3d")  # short/ambiguous - require word boundaries
RESEARCH_KEYWORDS = ("research",)
ENTREPRENEURSHIP_KEYWORDS = (
    "entrepreneur", "startup", "venture capital", "pitch competition", "innovation lab",
)


def _substring_matcher(keywords):
    return lambda text: any(kw in text for kw in keywords)


def _tech_event_matcher(text):
    if any(phrase in text for phrase in TECH_EVENT_PHRASES):
        return True
    return any(re.search(rf"\b{token}\b", text) for token in TECH_EVENT_WORD_TOKENS)


# Precedence, most specific evidence first. tech_event/research/
# entrepreneurship sit after every ASU Career Services subtype so V0.8A
# classifications are completely unaffected.
_RULES = (
    (_substring_matcher(CAREER_FAIR_KEYWORDS), "career_fair"),
    (_substring_matcher(EMPLOYER_EVENT_KEYWORDS), "employer_event"),
    (_substring_matcher(NETWORKING_KEYWORDS), "networking"),
    (_substring_matcher(INFO_SESSION_KEYWORDS), "info_session"),
    (_substring_matcher(WORKSHOP_KEYWORDS), "workshop"),
    (_substring_matcher(CAREER_SERVICES_KEYWORDS), "career_services"),
    (_tech_event_matcher, "tech_event"),
    (_substring_matcher(RESEARCH_KEYWORDS), "research"),
    (_substring_matcher(ENTREPRENEURSHIP_KEYWORDS), "entrepreneurship"),
)


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def classify_asu_event(event):
    """Classify one ASU event into exactly one subtype, using name/
    description text evidence only. Deterministic keyword precedence:
    career_fair > employer_event > networking > info_session > workshop >
    career_services > tech_event > research > entrepreneurship > other. No
    DB/HTTP access, no mutation.
    """
    text = " ".join(filter(None, [_get(event, "name"), _get(event, "description")])).lower()

    for matcher, subtype in _RULES:
        if matcher(text):
            return subtype
    return "other"
