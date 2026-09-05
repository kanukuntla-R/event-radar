"""Deterministic, transparent relevance policy for broad discovery sources
(ASU Events). This is NOT machine learning, personalization, or scoring -
it's a documented, keyword/category rule set answering one question: is
this event useful to a CS/tech student looking for career, technical, or
professional-development opportunities? No DB access, no mutation.
"""
import re

# Strong, unambiguous career/professional signals - present -> include,
# regardless of category.
STRONG_INCLUDE_KEYWORDS = (
    "career fair", "internship", "employer", "recruiter", "networking",
    "career development", "resume", "interview", "job", "hiring",
    "professional development",
)

# Technical relevance signals - present (with no exclude evidence) -> include.
TECHNICAL_INCLUDE_KEYWORDS = (
    "software", "computer science", "computing", "artificial intelligence",
    "machine learning", "cybersecurity", "security", "cloud", "data science",
    "engineering", "developer", "programming", "technology", "nvidia",
    "technical", "research computing", "entrepreneurship", "startup", "innovation",
)
TECHNICAL_WORD_TOKENS = ("ai", "ml")  # short/ambiguous - require word boundaries

STRONG_EXCLUDE_KEYWORDS = (
    "yoga", "wellness", "health training", "graduation", "family event",
    "game watch", "art exhibition", "movie screening", "tailgate", "pep rally",
)

# Source-provided category strings (asuevents.asu.edu), lower-cased.
STRONG_EXCLUDE_CATEGORIES = {
    "sports", "health and wellness", "graduation", "family friendly", "arts and entertainment",
}
# Supporting evidence only - never enough alone to include, even
# "Professional and Career Development". Live testing showed ASU applies
# that category broadly (e.g. to a chocolate-science talk with no other
# career/technical evidence), so a *category* tag alone never triggers
# include here - only a real name/description keyword or technical signal
# does. The category still counts as supporting evidence for review/signal
# reporting.
SUPPORTING_CATEGORIES = {"science", "academic events", "professional and career development"}


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _normalize_terms(terms):
    return {t.strip().lower() for t in (terms or []) if t and t.strip()}


def _has_technical_signal(text):
    if any(kw in text for kw in TECHNICAL_INCLUDE_KEYWORDS):
        return True
    return any(re.search(rf"\b{token}\b", text) for token in TECHNICAL_WORD_TOKENS)


def classify_relevance(event):
    """Decide include/review/exclude for one discovered event candidate.

    `event` should carry `name`, `description`, `categories` (a list of
    source-provided category strings), and optionally `audiences` (a list
    of source-provided audience strings - recorded as a signal only, never
    enough by itself to include or exclude).

    Returns {"decision": "include"|"review"|"exclude", "reason": str,
    "signals": [str, ...]}. Purely deterministic - the same input always
    produces the same output. No ML, no personalization, no DB/HTTP access.

    Policy, most decisive evidence first:
      1. A strong exclude category/keyword together with a strong career or
         technical signal is conflicting evidence -> review (never a silent
         override in either direction).
      2. A strong exclude category/keyword alone -> exclude.
      3. A strong career/professional keyword (in the name/description text)
         -> include.
      4. A technical keyword (with no exclude evidence) -> include.
      5. A supporting-only category (science/academic events/professional
         and career development) with nothing else -> review (a category
         tag alone is never enough - see SUPPORTING_CATEGORIES).
      6. No evidence at all -> review (conservative default; never silently
         include, and never silently exclude a genuinely unknown event).
    """
    name = _get(event, "name") or ""
    description = _get(event, "description") or ""
    text = f"{name} {description}".lower()
    categories = _normalize_terms(_get(event, "categories"))
    audiences = _normalize_terms(_get(event, "audiences"))

    signals = []

    exclude_categories_hit = categories & STRONG_EXCLUDE_CATEGORIES
    exclude_keyword_hit = next((kw for kw in STRONG_EXCLUDE_KEYWORDS if kw in text), None)
    for cat in sorted(exclude_categories_hit):
        signals.append(f"exclude_category:{cat}")
    if exclude_keyword_hit:
        signals.append(f"exclude_keyword:{exclude_keyword_hit}")

    strong_include_hit = next((kw for kw in STRONG_INCLUDE_KEYWORDS if kw in text), None)
    if strong_include_hit:
        signals.append(f"include_keyword:{strong_include_hit}")

    technical_hit = _has_technical_signal(text)
    if technical_hit:
        signals.append("technical_signal")

    supporting_category_hit = categories & SUPPORTING_CATEGORIES
    for cat in sorted(supporting_category_hit):
        signals.append(f"supporting_category:{cat}")

    if audiences:
        signals.append(f"audience:{','.join(sorted(audiences))}")

    has_exclude = bool(exclude_categories_hit or exclude_keyword_hit)
    has_strong_signal = bool(strong_include_hit or technical_hit)

    if has_exclude and has_strong_signal:
        return {"decision": "review", "reason": "conflicting career/technical and exclude signals", "signals": signals}
    if has_exclude:
        return {"decision": "exclude", "reason": "matched an exclude category or keyword", "signals": signals}
    if strong_include_hit:
        return {"decision": "include", "reason": "strong career/professional signal", "signals": signals}
    if technical_hit:
        return {"decision": "include", "reason": "technical/professional relevance", "signals": signals}
    if supporting_category_hit:
        return {"decision": "review", "reason": "supporting category alone is not enough to include", "signals": signals}
    return {"decision": "review", "reason": "no clear include or exclude evidence", "signals": signals}
