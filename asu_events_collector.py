"""Read-only asuevents.asu.edu (university-wide ASU Events) discovery
collector. Independent from asu_career_collector.py - a different site with
a different (Drupal Views) HTML structure, so it is not forced through the
same parser and does not share state with it.

This site's unfiltered listing is far larger and noisier than ASU Career
Services (sports, arts, graduation, etc.), so by default this collector
requests the site's own category filter rather than downloading everything
and filtering client-side. That filter narrows the *volume* fetched; the
independent asu_event_relevance.classify_relevance() still re-checks every
candidate that comes through as the real safety net (see
asu_events_discovery_runner.py) - the source-side filter is an optimization,
not the only line of defense.
"""
import html
import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen

USER_AGENT = "EventRadar/0.8 (personal event discovery; read-only ASU Events checker)"
DEFAULT_TIMEOUT = 10
BASE_URL = "https://asuevents.asu.edu"

# Source-provided category term IDs, observed directly on asuevents.asu.edu
# (its "Categories" nav, 2026-09) - isolated as named constants rather than
# bare numbers, and never assumed stable without this documentation.
CATEGORY_PROFESSIONAL_AND_CAREER_DEVELOPMENT = 1410
CATEGORY_SCIENCE = 1411
DEFAULT_CATEGORY_IDS = (CATEGORY_PROFESSIONAL_AND_CAREER_DEVELOPMENT, CATEGORY_SCIENCE)

# ponytail: conservative default - the combined filter alone spans 10+ pages
# of 24 events each; raise only if a genuine need for deeper paging shows up.
DEFAULT_MAX_PAGES = 3

ITEM_RE = re.compile(r'<li class="card cards-components card-event">(.*?)</li>', re.IGNORECASE | re.DOTALL)
TITLE_RE = re.compile(r'class="[^"]*card-title[^"]*"><a href="([^"]+)">(.*?)</a>', re.IGNORECASE | re.DOTALL)
DATE_RE = re.compile(r'views-field-field-event-date-value-1"><span class="field-content">(.*?)</span>', re.IGNORECASE | re.DOTALL)
TIME_RE = re.compile(r'views-field-field-event-date-end-value">\s*<span class="field-content">(.*?)</span>\s*</div>', re.IGNORECASE | re.DOTALL)
LOCATION_RE = re.compile(r'views-field-field-asu-events-location[^"]*"><div class="field-content">(.*?)</div>', re.IGNORECASE | re.DOTALL)

CATEGORY_PILLS_RE = re.compile(r'event-pills-detail"><ul>(.*?)</ul>', re.IGNORECASE | re.DOTALL)
PILL_ITEM_RE = re.compile(r"<li>(.*?)</li>", re.IGNORECASE | re.DOTALL)
DESCRIPTION_RE = re.compile(r'views-field-body"><div class="field-content">(.*?)</div>\s*</div>', re.IGNORECASE | re.DOTALL)
CTA_RE = re.compile(r'event-cta"><a href="([^"]+)"', re.IGNORECASE)
DETAIL_LOCATION_RE = re.compile(r'event-location"[^>]*>.*?<h2>(.*?)</h2>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text):
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()


def _default_fetch(url, timeout):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _listing_url(category_ids, page):
    params = "&".join(f"category%5B{cid}%5D={cid}" for cid in category_ids)
    return f"{BASE_URL}/?{params}&page={page}"


def _parse_listing_page(html_text, page_url):
    candidates = []
    for block in ITEM_RE.findall(html_text):
        title_match = TITLE_RE.search(block)
        if not title_match:
            continue
        href, title_html = title_match.groups()
        title = _strip_tags(title_html)
        if not title:
            continue

        date_match = DATE_RE.search(block)
        time_match = TIME_RE.search(block)
        location_match = LOCATION_RE.search(block)

        candidates.append({
            "name": title,
            "date_text": _strip_tags(date_match.group(1)) if date_match else None,
            "time_text": _strip_tags(time_match.group(1)) if time_match else None,
            "location_text": _strip_tags(location_match.group(1)) if location_match else None,
            # href attributes carry literal "&amp;" between query params -
            # unescape before the URL is used for anything (dedup, storage).
            "source_url": urljoin(page_url, html.unescape(href)),
        })
    return candidates


def _fetch_detail(url, timeout, fetch):
    try:
        html_text = fetch(url, timeout)
    except Exception:
        return {}

    enrichment = {}

    pills_match = CATEGORY_PILLS_RE.search(html_text)
    if pills_match:
        categories = [_strip_tags(li) for li in PILL_ITEM_RE.findall(pills_match.group(1))]
        enrichment["categories"] = [c for c in categories if c]

    description_match = DESCRIPTION_RE.search(html_text)
    if description_match:
        enrichment["description"] = _strip_tags(description_match.group(1))

    cta_match = CTA_RE.search(html_text)
    if cta_match:
        enrichment["registration_url"] = html.unescape(cta_match.group(1))

    location_match = DETAIL_LOCATION_RE.search(html_text)
    if location_match:
        precise_location = _strip_tags(location_match.group(1))
        if precise_location:
            enrichment["location_text"] = precise_location

    return enrichment


def collect_listing(category_ids=None, fetch_func=None, now=None, max_pages=DEFAULT_MAX_PAGES,
                     fetch_detail=True, timeout=DEFAULT_TIMEOUT):
    """Discover event candidates from asuevents.asu.edu.

    Requests `?category[...]=...&page=N` pages (0-indexed) it constructs
    itself for `category_ids` (defaults to DEFAULT_CATEGORY_IDS), stopping
    at `max_pages` or as soon as a page returns zero event cards (no more
    results) - never an infinite or historical crawl. For each listing hit,
    when `fetch_detail` is true, fetches that event's own detail page
    exactly once to add categories, a fuller description, a registration
    link, and a more precise location when present - never more than one
    extra request per candidate, and no other link on that page is followed.

    `fetch_func(url, timeout) -> html_text` may be injected for fully
    offline tests; defaults to a stdlib urlopen fetch. A page or detail
    fetch failure is isolated (stops pagination, or leaves that candidate
    un-enriched), never raised.

    Returns {"candidates": [raw evidence dicts], "pages_fetched": int}. Raw
    evidence is not yet normalized/classified - see
    asu_events_normalizer.normalize_candidate and asu_event_relevance.classify_relevance.
    """
    fetch = fetch_func or _default_fetch
    category_ids = category_ids or DEFAULT_CATEGORY_IDS

    candidates = []
    pages_fetched = 0
    seen_urls = set()

    for page in range(max_pages):
        page_url = _listing_url(category_ids, page)
        if page_url in seen_urls:
            break
        seen_urls.add(page_url)
        try:
            html_text = fetch(page_url, timeout)
        except Exception:
            break
        pages_fetched += 1

        page_candidates = _parse_listing_page(html_text, page_url)
        if not page_candidates:
            break
        candidates.extend(page_candidates)

    if fetch_detail:
        for candidate in candidates:
            candidate.update(_fetch_detail(candidate["source_url"], timeout, fetch))

    return {"candidates": candidates, "pages_fetched": pages_fetched}
