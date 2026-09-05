"""Pure helpers for classifying and ranking candidate replacement source
URLs. No I/O, no DB access, no mutation of the inputs - only deterministic
rules (no ML/embeddings). This module never decides to replace anything; it
just turns a raw discovery hit into a structured, storable candidate.
"""
import re
from urllib.parse import urlsplit, urlunsplit

SOCIAL_DOMAINS = ("twitter.com", "x.com", "instagram.com", "facebook.com", "linkedin.com", "discord.com", "discord.gg")
MLH_DOMAINS = ("mlh.io",)

# Baseline confidence per candidate_type, before any branding-match bump.
BASE_CONFIDENCE = {
    "official_event": "high",
    "official_university": "high",
    "mlh": "medium",
    "official_organizer": "medium",
    "official_social": "low",
    "other": "low",
}


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _domain(url):
    if not url:
        return ""
    return urlsplit(url).netloc.lower().split(":")[0]


def _registrable_domain(domain):
    # ponytail: last two labels is good enough for this project's domains
    # (no public-suffix-list handling) - revisit if a co.uk-style host shows up.
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def _significant_tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) >= 4]


def normalize_url(url):
    """Deterministic URL normalization for deduplication/comparison:
    lower-cases scheme/host, drops the fragment, and strips a trailing
    slash from the path (root "/" is kept). Returns None for falsy input.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def classify_candidate(event, candidate):
    """Classify a raw candidate hit into one of the 6 candidate_type values,
    using only the event's existing source_url/organizer for context.
    """
    url = candidate.get("candidate_url") or candidate.get("url")
    domain = _domain(url)
    existing_domain = _domain(_get(event, "source_url"))
    organizer_tokens = _significant_tokens(_get(event, "organizer"))

    if domain and existing_domain and _registrable_domain(domain) == _registrable_domain(existing_domain):
        return "official_event"
    if domain.endswith(".edu"):
        return "official_university"
    if any(mlh in domain for mlh in MLH_DOMAINS):
        return "mlh"
    if any(social in domain for social in SOCIAL_DOMAINS):
        return "official_social"
    if organizer_tokens and any(tok in domain for tok in organizer_tokens):
        return "official_organizer"
    return "other"


def _brand_match(event, title):
    event_tokens = _significant_tokens(_get(event, "name"))
    title_tokens = set(_significant_tokens(title))
    return any(tok in title_tokens for tok in event_tokens)


def rank_candidate(event, candidate):
    """Classify + score a raw discovery hit ({"candidate_url", "title",
    "evidence"}) into a structured candidate ready for
    source_recovery_store.store_recovery_candidates(). No DB writes; neither
    `event` nor `candidate` is mutated.
    """
    candidate_type = classify_candidate(event, candidate)
    confidence = BASE_CONFIDENCE[candidate_type]
    title = candidate.get("title")

    if confidence == "medium" and _brand_match(event, title):
        confidence = "high"

    return {
        "candidate_url": normalize_url(candidate.get("candidate_url") or candidate.get("url")),
        "candidate_type": candidate_type,
        "title": title,
        "evidence": candidate.get("evidence"),
        "confidence": confidence,
    }
