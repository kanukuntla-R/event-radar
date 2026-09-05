"""Read-only source-recovery discovery: given a hackathon with an unhealthy
source, look for plausible replacement URLs. Deliberately narrow - this is
NOT a crawler: it fetches the event's current source_url at most once and
only reports the final redirect target plus outbound links on that single
page that look official (university/.edu, MLH, known social platforms, or
the organizer's own name in the domain). It never follows a link beyond that
one hop and never scrapes a search engine.

Does not modify hackathon_collector.py's behavior or share its state; this
is a separate, narrower fetch used only for recovery discovery.
"""
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

USER_AGENT = "EventRadar/0.7 (personal hackathon watchlist; read-only source-recovery checker)"
DEFAULT_TIMEOUT = 10

SOCIAL_DOMAINS = ("twitter.com", "x.com", "instagram.com", "facebook.com", "linkedin.com", "discord.com", "discord.gg")
MLH_DOMAINS = ("mlh.io",)

LINK_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def _get(event, key, default=None):
    try:
        value = event[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _strip_tags(text):
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()


def _domain(url):
    if not url:
        return ""
    return urlsplit(url).netloc.lower().split(":")[0]


def _organizer_tokens(organizer):
    return [t for t in re.findall(r"[a-z0-9]+", (organizer or "").lower()) if len(t) >= 4]


def _looks_official(domain, organizer_tokens):
    if not domain:
        return False
    if domain.endswith(".edu"):
        return True
    if any(mlh in domain for mlh in MLH_DOMAINS):
        return True
    if any(social in domain for social in SOCIAL_DOMAINS):
        return True
    return any(tok in domain for tok in organizer_tokens)


def _default_fetch(url, timeout):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        body = response.read()
        final_url = response.geturl()
    return body.decode("utf-8", errors="replace"), final_url


def discover_candidates(event, timeout=DEFAULT_TIMEOUT, now=None, fetch=None):
    """Look for plausible replacement source URLs for `event`. Read-only,
    single-hop: fetches `event`'s current source_url (if any) once and
    inspects it for a redirect target and outbound "official-looking" links.

    `fetch(url, timeout) -> (html_text, final_url)` may be injected for
    fully offline tests; defaults to a stdlib urlopen fetch. If the source
    has no URL, or the fetch fails for any reason (the exact situation
    recovery is needed for), returns [] rather than raising - discovery
    found nothing usable, which is not an error.

    Returns a list of raw candidate hits: {"candidate_url", "title",
    "evidence"} - not yet classified/scored (see source_recovery.rank_candidate).
    """
    fetch = fetch or _default_fetch
    source_url = _get(event, "source_url")
    if not source_url:
        return []

    try:
        html_text, final_url = fetch(source_url, timeout)
    except Exception:
        return []

    candidates = []
    seen = set()
    source_domain = _domain(source_url)
    organizer_tokens = _organizer_tokens(_get(event, "organizer"))

    if final_url and _domain(final_url) != source_domain:
        candidates.append({
            "candidate_url": final_url,
            "title": None,
            "evidence": f"Current source_url redirects here (from {source_url}).",
        })
        seen.add(final_url)

    for href, link_text in LINK_RE.findall(html_text or ""):
        absolute = urljoin(final_url or source_url, href)
        domain = _domain(absolute)
        if domain == source_domain or absolute in seen:
            continue
        if not _looks_official(domain, organizer_tokens):
            continue
        visible = _strip_tags(link_text)
        candidates.append({
            "candidate_url": absolute,
            "title": visible or None,
            "evidence": f"Linked from current source page as {visible or href!r}.",
        })
        seen.add(absolute)

    return candidates
