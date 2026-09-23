"""Deterministic, transparent, source-agnostic relevance policy for Event
Radar's discovery pipeline (ASU Events, Arizona Technology Council, and any
future source - Venture Cafe, Meetup, Luma, Eventbrite, ...). This is NOT
machine learning, personalization, or DB/HTTP access - it's a documented,
evidence-scoring rule set answering one question: is this event useful to
someone primarily interested in technology (CS/software/cybersecurity/AI/ML/
data/cloud/networking/DevOps/robotics/embedded/hackathons/tech careers) or
business/entrepreneurship (startups/founders/VC/accelerators/product/pitch
events/relevant professional networking)?

SCORING MODEL (replaces the old flat "any strong keyword -> include" rule):
evidence is grouped into four positive categories and one exclude category.
Each category counts *distinct* matched phrases, caps the count so repeating
the same idea doesn't inflate the score, then multiplies by a weight:

    category   weight  cap  meaning
    tech       3       2    unambiguous technology phrases/words
    business   3       2    unambiguous career/startup/business phrases
    format     1       2    event-shape words (workshop/meetup/conference/...)
    weak       1       3    ambiguous words that mean something only in
                             combination (engineering, data, cloud, security,
                             network/networking, business, innovation,
                             investor/vc, professional development, ...)

`score = tech_score + business_score + format_score + weak_score`.

INCLUDE requires BOTH `score >= INCLUDE_THRESHOLD` (3) AND at least one
*strong* tech or business phrase (`has_strong_signal`). The strong-signal
requirement is load-bearing, not redundant with the score check: with the
weights above a single strong phrase already scores exactly 3, so today
`has_strong_signal` alone would decide it - but weak/format evidence must
NEVER be enough by itself, no matter how it combines. Three ambiguous words
like "business" + "networking" + "innovation" (a real false-positive found
in adversarial testing - "Business Innovation Networking Conference") also
sum to a score of 3 under the raw weights, and that is NOT convincing
evidence of technology/business-startup relevance - it's exactly the
generic-buzzword-conference case this task exists to catch. So weak/format
evidence alone, at any score, caps out at review; only a genuine strong
phrase can cross into include. (An earlier version of this model let three
weak signals alone reach include - removed after the adversarial audit in
test_event_relevance.py's AdversarialFalsePositiveTests; see that class for
the full list of previously-misclassified generic examples.)

A strong exclude category/keyword match short-circuits the score: if there
is also *any* topical evidence (tech, business, or weak - format words like
"workshop" don't count, they say nothing about topic) it's conflicting
evidence -> review, never a silent override in either direction. With zero
topical evidence, a strong exclude match -> exclude outright.

Zero evidence at all -> review (conservative default; never silently include
or silently exclude a genuinely unknown event).

FALSE-POSITIVE PROTECTIONS: every phrase/word is matched with regex word
boundaries (`\\b...\\b`, multi-word phrases allow flexible whitespace) - never
a naive substring test. This means "cloud" cannot match "cloudy", "ai" cannot
match "maintain"/"said", "security" alone never fires just because
"insecurity" appears, and "networking" (career sense, weak evidence) is a
distinct token from "computer networking" (tech phrase, strong evidence) -
"network"/"networking" alone stay weak/ambiguous exactly because "networking
event" and "computer networking" are different concepts.

LOCATION is out of scope here entirely - this module never reads location
fields and never affects them. See event_location.py.

Returns from classify_relevance(): {"decision": "include"|"review"|"exclude",
"reason": str, "signals": [str, ...], "matched_topics": [str, ...],
"score": int}. `signals` (list[str], joinable) and `reason` (str) keep the
exact shape the dashboard/review-store already render - see
templates/index.html's `review_signals_list` filter and event_review_store.py.
`matched_topics` and `score` are new, additive explainability fields for a
future "why this matched" dashboard view.
"""
import re

INCLUDE_THRESHOLD = 3

CATEGORY_WEIGHTS = {"tech": 3, "business": 3, "format": 1, "weak": 1}
CATEGORY_CAPS = {"tech": 2, "business": 2, "format": 2, "weak": 3}

# Unambiguous technology phrases/words - each alone is decisive (weight 3).
# Deliberately NOT a giant keyword dump: multi-word phrases disambiguate the
# genuinely ambiguous short words (see WEAK_AMBIGUOUS_PHRASES below) rather
# than adding more bare words here.
TECH_STRONG_PHRASES = (
    "computer science", "software engineering", "software development",
    "software developer", "web development", "app development", "mobile development",
    "cybersecurity", "cyber security", "information security", "application security",
    "network security", "computer networking", "network engineering",
    "artificial intelligence", "machine learning", "deep learning", "generative ai",
    "large language model", "llm", "ai", "ml",
    "data science", "data engineering", "data analytics", "data engineer", "data scientist", "big data",
    "cloud computing", "cloud security", "cloud native", "cloud infrastructure", "cloud engineer",
    "devops", "site reliability", "sre",
    "robotics", "embedded systems", "embedded hardware", "embedded",
    "internet of things", "iot",
    "hackathon", "coding bootcamp", "open source software",
    "kubernetes", "docker", "research computing", "computer engineering",
    "nvidia", "github",
    "software", "developer", "programming", "technical", "technology", "tech",
)

# Unambiguous career/startup/business phrases - each alone is decisive.
BUSINESS_STRONG_PHRASES = (
    "career fair", "internship", "recruiter", "employer", "hiring", "resume", "interview",
    "career development", "startup", "entrepreneurship", "founder", "accelerator", "incubator",
    "demo day", "pitch competition", "pitch night",
)

# Event-shape words: say nothing about topic by themselves (a "workshop" or
# "conference" could be about anything), so low weight and never enough
# alone, and never treated as topical evidence for the exclude/conflict check.
EVENT_FORMAT_PHRASES = (
    "workshop", "hackathon", "meetup", "conference", "summit", "bootcamp", "webinar",
    "symposium", "expo", "career fair", "job fair",
)

# Ambiguous words/phrases - the "AI"/"data"/"network"/"cloud"/"security"/
# "engineering"/"business"/"innovation" cases the task calls out. Each means
# something concrete only combined with other evidence (three-plus of these,
# or one plus a strong signal); see the module docstring's scoring model.
WEAK_AMBIGUOUS_PHRASES = (
    "engineering", "engineer", "data", "cloud", "security", "network", "networking",
    "business", "innovation", "innovative", "investor", "vc", "venture capital",
    "professional development", "product management", "pitch",
)

# Source-provided category strings (ASU taxonomy). AZ Tech Council's own
# category slugs never match this set - a harmless no-op there, not a
# source-specific branch (see az_tech_council_discovery_runner.py).
STRONG_EXCLUDE_CATEGORIES = {
    "sports", "health and wellness", "graduation", "family friendly", "arts and entertainment",
}
# Category tags that are supporting evidence only - counted as weak, never
# enough alone to include (ASU applies "Professional and Career Development"
# broadly, e.g. to a chocolate-science talk with no other career/technical
# evidence - see test_asu_event_relevance.py history).
SUPPORTING_CATEGORIES = {"science", "academic events", "professional and career development"}

# Clearly unrelated topics (wellness/fitness, arts/crafts, unrelated
# entertainment/social, unrelated healthcare, K-12-only, unrelated
# nonprofit/community programming) - representative phrases, not an
# exhaustive enumeration.
STRONG_EXCLUDE_PHRASES = (
    "yoga", "wellness", "fitness class", "meditation", "workout session", "health training",
    "art exhibition", "craft fair", "pottery class", "painting class",
    "movie screening", "game watch", "tailgate", "pep rally", "concert", "comedy show",
    "trivia night", "karaoke night",
    "health screening", "medical clinic", "vaccination clinic", "blood drive",
    "elementary school", "middle school", "kindergarten", "k-12", "family event",
    "food drive", "clothing drive", "community cleanup", "graduation",
)


# "ml" alone is excluded from the plural-friendly "s?" suffix below - "mls"
# is a real, unrelated word in practice (MLS, the soccer league), and "ml"
# has no legitimate plural form the way "recruiter"/"founder"/"vc" do.
_NO_PLURAL_SUFFIX = {"ml"}


def _compile(phrases):
    compiled = []
    for phrase in phrases:
        pattern = re.escape(phrase).replace(r"\ ", r"\s+")
        suffix = "" if phrase in _NO_PLURAL_SUFFIX else "s?"
        compiled.append((phrase, re.compile(rf"\b{pattern}{suffix}\b")))
    return compiled


_TECH_RX = _compile(TECH_STRONG_PHRASES)
_BUSINESS_RX = _compile(BUSINESS_STRONG_PHRASES)
_FORMAT_RX = _compile(EVENT_FORMAT_PHRASES)
_WEAK_RX = _compile(WEAK_AMBIGUOUS_PHRASES)
_EXCLUDE_RX = _compile(STRONG_EXCLUDE_PHRASES)


def _matches(text, compiled):
    return [label for label, rx in compiled if rx.search(text)]


def _dedupe(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _normalize_terms(terms):
    return {t.strip().lower() for t in (terms or []) if t and t.strip()}


def _score(count, category):
    return min(count, CATEGORY_CAPS[category]) * CATEGORY_WEIGHTS[category]


def classify_relevance(event):
    """Decide include/review/exclude for one discovered event candidate.

    `event` should carry `name`, `description`, `categories` (a list of
    source-provided category strings), and optionally `audiences` (a list
    of source-provided audience strings - supporting/weak evidence only,
    never enough by itself to include or exclude).

    Returns {"decision": "include"|"review"|"exclude", "reason": str,
    "signals": [str, ...], "matched_topics": [str, ...], "score": int}.
    Purely deterministic - the same input always produces the same output.
    No ML, no personalization, no DB/HTTP access. See the module docstring
    for the full scoring model.
    """
    name = _get(event, "name") or ""
    description = _get(event, "description") or ""
    text = f"{name} {description}".lower()
    categories = _normalize_terms(_get(event, "categories"))
    audiences = _normalize_terms(_get(event, "audiences"))

    tech_hits = _matches(text, _TECH_RX)
    business_hits = _matches(text, _BUSINESS_RX)
    format_hits = _matches(text, _FORMAT_RX)
    weak_hits = list(_matches(text, _WEAK_RX))

    supporting_category_hits = sorted(categories & SUPPORTING_CATEGORIES)
    weak_hits.extend(supporting_category_hits)
    if audiences:
        weak_hits.append("audience")

    exclude_category_hits = sorted(categories & STRONG_EXCLUDE_CATEGORIES)
    exclude_phrase_hits = _matches(text, _EXCLUDE_RX)
    exclude_hits = exclude_category_hits + exclude_phrase_hits

    tech_score = _score(len(tech_hits), "tech")
    business_score = _score(len(business_hits), "business")
    format_score = _score(len(format_hits), "format")
    weak_score = _score(len(weak_hits), "weak")
    score = tech_score + business_score + format_score + weak_score

    matched_topics = _dedupe(tech_hits + business_hits + format_hits + weak_hits)
    topical_positive = bool(tech_score or business_score or weak_score)
    has_strong_signal = bool(tech_hits or business_hits)

    signals = []
    for cat in exclude_category_hits:
        signals.append(f"exclude_category:{cat}")
    for kw in exclude_phrase_hits:
        signals.append(f"exclude_keyword:{kw}")
    for kw in tech_hits:
        signals.append(f"tech:{kw}")
    for kw in business_hits:
        signals.append(f"business:{kw}")
    for kw in format_hits:
        signals.append(f"format:{kw}")
    for kw in weak_hits:
        signals.append(f"weak:{kw}")
    if audiences:
        signals.append(f"audience:{','.join(sorted(audiences))}")

    if exclude_hits:
        if topical_positive:
            return {
                "decision": "review",
                "reason": f"conflicting exclude signal ({', '.join(exclude_hits)}) alongside topic evidence ({', '.join(matched_topics)})",
                "signals": signals, "matched_topics": matched_topics, "score": score,
            }
        return {
            "decision": "exclude",
            "reason": f"matched exclude signal(s): {', '.join(exclude_hits)}",
            "signals": signals, "matched_topics": matched_topics, "score": score,
        }

    if has_strong_signal and score >= INCLUDE_THRESHOLD:
        return {
            "decision": "include",
            "reason": f"matched {', '.join(matched_topics)} (score {score} >= {INCLUDE_THRESHOLD})",
            "signals": signals, "matched_topics": matched_topics, "score": score,
        }

    if matched_topics and not has_strong_signal:
        reason = f"only weak/ambiguous or event-format evidence, no strong technology or business signal; matched {', '.join(matched_topics)}"
    elif matched_topics:
        reason = f"insufficient evidence to include (score {score} < {INCLUDE_THRESHOLD}); matched {', '.join(matched_topics)}"
    else:
        reason = "no clear include or exclude evidence"
    return {
        "decision": "review", "reason": reason,
        "signals": signals, "matched_topics": matched_topics, "score": score,
    }
