"""Read-only ASU Career Services event-listing DISCOVERY collector.

This is a different job from hackathon_collector.py, which re-checks one
already-known event's own source. This module finds NEW event candidates:
it parses the listing page's `event_item` entries, follows bounded
`/page/N/` pagination, and optionally fetches each candidate's own detail
page once to enrich it (description, RSVP link). It never crawls arbitrary
links and never scrapes a search engine.
"""
import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen

USER_AGENT = "EventRadar/0.8 (personal event discovery; read-only ASU Career Services checker)"
DEFAULT_TIMEOUT = 10
DEFAULT_LISTING_URL = "https://career.eoss.asu.edu/events/"
# ponytail: conservative bound on a WordPress-style /page/N/ archive - raise
# if the current/upcoming window genuinely needs deeper paging.
DEFAULT_MAX_PAGES = 3

ITEM_RE = re.compile(
    r'<li class="event_item[^"]*">\s*<a href="([^"]+)">(.*?)</a>\s*</li>',
    re.IGNORECASE | re.DOTALL,
)
TITLE_RE = re.compile(r'<h3 class="title">(.*?)</h3>', re.IGNORECASE | re.DOTALL)
DATE_TEXT_RE = re.compile(r'<p class="event-date">(.*?)</p>', re.IGNORECASE | re.DOTALL)
TIME_TEXT_RE = re.compile(r'<p class="event-time">(.*?)</p>', re.IGNORECASE | re.DOTALL)
LOCATION_TEXT_RE = re.compile(r'<p class="event-location">(.*?)</p>', re.IGNORECASE | re.DOTALL)
NEXT_PAGE_RE = re.compile(
    r'<a class="page-numbers"[^>]*href="([^"]+)"[^>]*>\s*<span[^>]*>Page</span>\s*(\d+)\s*</a>',
    re.IGNORECASE,
)
ENTRY_CONTENT_RE = re.compile(r'<div class="entry-content">(.*?)</div>', re.IGNORECASE | re.DOTALL)
RSVP_RE = re.compile(r'<a[^>]*class="[^"]*rsvp-button[^"]*"[^>]*href="([^"]+)"', re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text):
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()


def _default_fetch(url, timeout):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _parse_listing_page(html_text, page_url):
    candidates = []
    for href, body in ITEM_RE.findall(html_text):
        title_match = TITLE_RE.search(body)
        title = _strip_tags(title_match.group(1)) if title_match else ""
        title = re.sub(r"^Event:\s*", "", title).strip()
        if not title:
            continue

        date_match = DATE_TEXT_RE.search(body)
        time_match = TIME_TEXT_RE.search(body)
        location_match = LOCATION_TEXT_RE.search(body)

        candidates.append({
            "name": title,
            "date_text": _strip_tags(date_match.group(1)) if date_match else None,
            "time_text": _strip_tags(time_match.group(1)) if time_match else None,
            "location_text": _strip_tags(location_match.group(1)) if location_match else None,
            "source_url": urljoin(page_url, href),
        })
    return candidates


def _find_next_page(html_text, current_page, base_url):
    pages = {int(num): urljoin(base_url, href) for href, num in NEXT_PAGE_RE.findall(html_text)}
    return pages.get(current_page + 1)


def _fetch_detail(url, timeout, fetch):
    try:
        html_text = fetch(url, timeout)
    except Exception:
        return {}

    enrichment = {}
    content_match = ENTRY_CONTENT_RE.search(html_text)
    if content_match:
        enrichment["description"] = _strip_tags(content_match.group(1))
    rsvp_match = RSVP_RE.search(html_text)
    if rsvp_match:
        enrichment["registration_url"] = rsvp_match.group(1)
    return enrichment


def collect_listing(url=None, fetch_func=None, now=None, max_pages=DEFAULT_MAX_PAGES,
                     fetch_detail=True, timeout=DEFAULT_TIMEOUT):
    """Discover event candidates from the ASU Career Services listing.

    Follows `/page/N/` pagination up to `max_pages`, stopping early if a
    page has no link to the next sequential page or if a page URL repeats
    (never an infinite loop). For each listing hit, when `fetch_detail` is
    true, fetches that event's own detail page exactly once to add a
    description and RSVP link when present - never more than one extra
    request per candidate, and never follows any other link found there.

    `fetch_func(url, timeout) -> html_text` may be injected for fully
    offline tests; defaults to a stdlib urlopen fetch. A page or detail
    fetch failure is isolated (that page/candidate is skipped, or pagination
    simply stops), never raised.

    Returns {"candidates": [raw evidence dicts], "pages_fetched": int}. Raw
    evidence is not yet normalized - see asu_event_normalizer.normalize_candidate.
    """
    fetch = fetch_func or _default_fetch
    listing_url = url or DEFAULT_LISTING_URL

    candidates = []
    seen_pages = set()
    page_url = listing_url
    page_number = 1
    pages_fetched = 0

    while page_url and page_number <= max_pages and page_url not in seen_pages:
        seen_pages.add(page_url)
        try:
            html_text = fetch(page_url, timeout)
        except Exception:
            break
        pages_fetched += 1

        candidates.extend(_parse_listing_page(html_text, page_url))
        page_url = _find_next_page(html_text, page_number, listing_url)
        page_number += 1

    if fetch_detail:
        for candidate in candidates:
            candidate.update(_fetch_detail(candidate["source_url"], timeout, fetch))

    return {"candidates": candidates, "pages_fetched": pages_fetched}
