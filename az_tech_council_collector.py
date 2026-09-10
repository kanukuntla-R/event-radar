"""Read-only aztechcouncil.org (Arizona Technology Council) events discovery
collector. Independent from every ASU collector - a different site, a
different theme/markup, and no shared state with them.

INGESTION METHOD (V0.12A phase-1 research, see docs/ for the session
report): checked, in priority order, for an official ICS feed (none found),
a structured JSON/API representation, embedded JSON-LD, and stable HTML
listings/details.

The site *does* expose a public WordPress REST API for its "event" custom
post type (https://www.aztechcouncil.org/wp-json/wp/v2/event) - but it only
returns ~950 posts (the entire historical archive back to whenever the
"event" post type was introduced), ordered by post *publish* date, with no
REST support for filtering/ordering by the event's own date (its date/time/
venue live in ACF fields that are not exposed to REST, and an
`orderby=meta_value&meta_key=...` request 400s - the field isn't
REST-whitelisted). Fetching and per-event-detail-enriching all 950 archived
posts just to find the dozen or so genuinely upcoming ones would be far
slower and far less reliable than the alternative: the site's own public
`/events/` listing page already does exactly that server-side filtering (an
"Upcoming" vs "All" toggle, checked to Upcoming by default) and returns a
small, correctly-ordered (ascending by event date) HTML page - the same
page a human visitor or a search engine sees, no JavaScript required. No
JSON-LD Event markup was found either (only generic WebPage/Yoast SEO
schema). So this collector uses that public HTML listing (a stable,
official, human-facing representation) for discovery, plus one HTML detail
page per event - the exact same two-tier "listing + per-event detail
enrichment" shape asu_events_collector.py already uses for a different
site, for the same reason: the listing alone doesn't carry time/venue/
registration/category, but the detail page does.

Pagination note: the listing's visible "next page" control is driven by a
private, undocumented admin-ajax.php action (with its own nonce) - the
static `?paged=N` / `/events/page/N/` URL a browser falls back to without
JS was verified (2026-09) to render page 1's content again, not page 2's.
Rather than reverse-engineer that private AJAX contract (exactly the kind
of brittle scraping this project avoids), collect_listing() below treats
"a page returned zero NOT-YET-SEEN candidates" as the stop condition, same
spirit as asu_events_collector's "zero cards -> stop". Today that means
each category bucket contributes exactly one real page - correct for the
site's current small (~dozen) upcoming-event volume, and automatically
picks up real further pages if the site ever makes that pagination work
without JS.

Known gap - uncommon date/time text shapes (deliberately NOT special-cased
here): live validation (2026-09) surfaced two real events this site
describes with formats asu_event_normalizer's shared parse_date_range/
parse_time_range don't recognize - "September 16, 2026 Thru September 18,
2026" (a "Thru" range instead of a dash) and "4-7PM" (a single shared
meridiem for both ends of a time range, instead of "4PM-7PM"). Both
correctly fall back to their documented "unparseable -> None, never
fabricated" behavior, and validate_candidate/validate_review_candidate then
conservatively reject the resulting timeless/incomplete candidate rather
than storing a guessed time or a truncated date range - the intended
behavior, not a bug. These are generic text-parsing gaps in the *shared*
helpers (also usable by any future source), not something specific to AZ
Tech Council's markup, so no source-specific parsing hack was added here to
work around them; broadening the shared date/time parsing is a candidate
for a future, general "uncertain event data" improvement instead.
"""
import html
import re
from urllib.parse import urljoin
from urllib.request import Request, urlopen

USER_AGENT = "EventRadar/0.12 (personal event discovery; read-only AZ Tech Council checker)"
DEFAULT_TIMEOUT = 10
BASE_URL = "https://www.aztechcouncil.org"
LISTING_URL = f"{BASE_URL}/events/"

# Source-provided event_category taxonomy slugs (aztechcouncil.org, 2026-09
# - see https://www.aztechcouncil.org/wp-json/wp/v2/event_category). The
# site's own default listing view ("All AZTC Events", value="all_aztc" on
# its category radio group - encoded here as the sentinel "all_aztc", never
# sent as an actual event_cat query param) already aggregates the
# council's own Phoenix/Tucson/statewide/virtual events; "community-tech-
# events" is a genuinely SEPARATE, larger pool of externally-submitted
# community tech events not included in that default view (confirmed live:
# the two buckets return disjoint event lists). Both are fetched by
# default for reasonable discovery coverage without pulling in the
# niche "optics-valley" vertical.
CATEGORY_ALL_AZTC = "all_aztc"
CATEGORY_COMMUNITY_TECH = "community-tech-events"
DEFAULT_CATEGORY_SLUGS = (CATEGORY_ALL_AZTC, CATEGORY_COMMUNITY_TECH)

# ponytail: conservative default, matching asu_events_collector's own
# DEFAULT_MAX_PAGES - the "stop on zero new candidates" rule below means
# real fetches per category bucket are far fewer than this cap today; raise
# only if a genuine need for deeper paging (a working non-JS pagination
# contract) shows up.
DEFAULT_MAX_PAGES = 3

CARD_RE = re.compile(
    r'<a\s+href="([^"]+)"\s+class="post-card event-card[^"]*">(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
TITLE_RE = re.compile(r'<h3 class="card-title">(.*?)</h3>', re.IGNORECASE | re.DOTALL)
CARD_DATE_RE = re.compile(r'<p class="card-date">(.*?)</p>', re.IGNORECASE | re.DOTALL)
EXCERPT_RE = re.compile(r'<p class="card-excerpt">(.*?)</p>', re.IGNORECASE | re.DOTALL)

DETAIL_FIELD_RE = re.compile(
    r'<p class="detail-title">([^<]+)</p>\s*<p class="details-text">(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
CATEGORY_CLASS_RE = re.compile(r'class="post-content[^"]*"', re.IGNORECASE)
CATEGORY_TERM_RE = re.compile(r"event_category-([a-z0-9-]+)")
REGISTER_RE = re.compile(
    r'<a href="([^"]+)"[^>]*data-registration-button-tracking="">Register</a>',
    re.IGNORECASE,
)
DESCRIPTION_RE = re.compile(
    r'event-content-wrapper">(.*?)<div class="col-4 event-details">',
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
BR_SPLIT_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _strip_tags(text):
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()


def _clean_address_block(raw_html):
    """Detail-page Location field is several `<span>` fragments separated
    by `<br/>` (street / "City, ST ZIP" / a virtual-event instruction line)
    - joins them with ", " instead of a raw tag-strip, which would smash
    "Tempe" and ", AZ" together with no separator.
    """
    parts = []
    for line in BR_SPLIT_RE.split(raw_html):
        text = _strip_tags(line).strip(" ,")
        text = re.sub(r"\s*,\s*", ", ", text)
        if text:
            parts.append(text)
    return ", ".join(parts)


def _default_fetch(url, timeout):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _listing_url(category_slug, page):
    params = []
    if category_slug != CATEGORY_ALL_AZTC:
        params.append(f"event_cat={category_slug}")
    if page > 1:
        params.append(f"paged={page}")
    query = "&".join(params)
    return f"{LISTING_URL}?{query}" if query else LISTING_URL


def _parse_listing_page(html_text, page_url):
    candidates = []
    for href, block in CARD_RE.findall(html_text):
        title_match = TITLE_RE.search(block)
        if not title_match:
            continue
        title = _strip_tags(title_match.group(1))
        if not title:
            continue

        date_match = CARD_DATE_RE.search(block)
        excerpt_match = EXCERPT_RE.search(block)

        candidates.append({
            "name": title,
            "date_text": _strip_tags(date_match.group(1)) if date_match else None,
            "description": _strip_tags(excerpt_match.group(1)) if excerpt_match else None,
            "source_url": urljoin(page_url, html.unescape(href)),
        })
    return candidates


def _fetch_detail(url, timeout, fetch):
    try:
        html_text = fetch(url, timeout)
    except Exception:
        return {}

    enrichment = {}

    fields = {}
    for label, value_html in DETAIL_FIELD_RE.findall(html_text):
        fields[_strip_tags(label).lower()] = value_html

    if "date" in fields:
        date_text = _strip_tags(fields["date"])
        if date_text:
            enrichment["date_text"] = date_text
    if "time" in fields:
        time_text = _strip_tags(fields["time"])
        if time_text:
            enrichment["time_text"] = time_text
    if "venue" in fields:
        venue_text = _strip_tags(fields["venue"])
        if venue_text:
            enrichment["location_text"] = venue_text
    elif "location" in fields:
        location_text = _clean_address_block(fields["location"])
        if location_text:
            enrichment["location_text"] = location_text
    if "presented by" in fields:
        organizer = _strip_tags(fields["presented by"])
        if organizer:
            enrichment["organizer"] = organizer

    category_class_match = CATEGORY_CLASS_RE.search(html_text)
    if category_class_match:
        categories = CATEGORY_TERM_RE.findall(category_class_match.group(0))
        if categories:
            enrichment["categories"] = categories

    register_match = REGISTER_RE.search(html_text)
    if register_match:
        enrichment["registration_url"] = html.unescape(register_match.group(1))

    description_match = DESCRIPTION_RE.search(html_text)
    if description_match:
        description = _strip_tags(description_match.group(1))
        if description:
            enrichment["description"] = description

    return enrichment


def collect_listing(category_slugs=None, fetch_func=None, max_pages=DEFAULT_MAX_PAGES,
                     fetch_detail=True, timeout=DEFAULT_TIMEOUT):
    """Discover event candidates from aztechcouncil.org/events/.

    Requests the listing for each of `category_slugs` (defaults to
    DEFAULT_CATEGORY_SLUGS), one page at a time, stopping per bucket at
    `max_pages` or as soon as a page contributes zero candidates whose
    source_url hasn't already been seen this run (see module docstring for
    why - the site's non-JS pagination fallback currently just re-serves
    page 1). For each listing hit, when `fetch_detail` is true, fetches
    that event's own detail page exactly once to add/override time, venue,
    a fuller description, registration link, presenter/organizer, and
    category terms - never more than one extra request per candidate, and
    no other link on that page is followed.

    `fetch_func(url, timeout) -> html_text` may be injected for fully
    offline tests; defaults to a stdlib urlopen fetch. A page or detail
    fetch failure is isolated (stops that bucket's pagination, or leaves
    that candidate un-enriched), never raised - a network error or a single
    malformed event can never take down the whole run.

    Returns {"candidates": [raw evidence dicts], "pages_fetched": int}. Raw
    evidence is not yet normalized/classified - see
    az_tech_council_normalizer.normalize_candidate and
    asu_event_relevance.classify_relevance.
    """
    fetch = fetch_func or _default_fetch
    category_slugs = category_slugs or DEFAULT_CATEGORY_SLUGS

    candidates = []
    pages_fetched = 0
    seen_source_urls = set()

    for category_slug in category_slugs:
        for page in range(1, max_pages + 1):
            page_url = _listing_url(category_slug, page)
            try:
                html_text = fetch(page_url, timeout)
            except Exception:
                break
            pages_fetched += 1

            page_candidates = _parse_listing_page(html_text, page_url)
            new_candidates = [c for c in page_candidates if c["source_url"] not in seen_source_urls]
            if not new_candidates:
                break
            seen_source_urls.update(c["source_url"] for c in new_candidates)
            candidates.extend(new_candidates)

    if fetch_detail:
        for candidate in candidates:
            candidate.update(_fetch_detail(candidate["source_url"], timeout, fetch))

    return {"candidates": candidates, "pages_fetched": pages_fetched}
