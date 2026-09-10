"""Tests for az_tech_council_collector.collect_listing(). Fully offline:
fetch is always injected, no real network calls. Fixtures mirror the real
aztechcouncil.org markup observed live (2026-09) - see the collector's
module docstring for the phase-1 research this is based on.

Run with: python -m unittest test_az_tech_council_collector -v
"""
import unittest

from az_tech_council_collector import collect_listing, CATEGORY_COMMUNITY_TECH, LISTING_URL

CARD_TEMPLATE = """
            <a
        href="{href}"
                class="post-card event-card  ">
    <div class="card-image" style="background-image:url(x.png)"></div>
    <div class="card-content-wrapper">
        <h3 class="card-title">{title}</h3>
        <p class="card-date">{date_text}</p>
        <p class="card-excerpt">{excerpt}</p>
    </div>
</a>
"""

LISTING_TEMPLATE = """
<div class="feed-content" id="feed-content">
    <div class="feed-content-wrapper">
{cards}
    </div>
</div>
"""

DETAIL_TEMPLATE = """
<article class="post-content post-1 event type-event status-publish {category_classes}">
  <div class="col-8 event-content-wrapper">
    <p>{description}</p>
  </div>
  <div class="col-4 event-details">
    <div class="event-details-wrapper">
      {presented_by_block}
      <p class="detail-title">{location_label}</p>
      <p class="details-text">{location_html}</p>
      <p class="detail-title">Date</p>
      <p class="details-text"><span>{date_text}</span></p>
      <p class="detail-title">Time</p>
      <p class="details-text">{time_text}</p>
      <p class="detail-title">Cost</p>
      <p class="details-text">FREE EVENT</p>
      <a href="{registration_url}" class="button text-center " data-registration-button-tracking="">Register</a>
    </div>
  </div>
</article>
"""


def _card(href="https://www.aztechcouncil.org/event/an-event/", title="An Event",
          date_text="Sep 18, 2026", excerpt="Join us for networking..."):
    return CARD_TEMPLATE.format(href=href, title=title, date_text=date_text, excerpt=excerpt)


def _listing(cards_html):
    return LISTING_TEMPLATE.format(cards=cards_html)


def _detail(category_classes="event_category-community-tech-events",
            description="A longer description of the event.",
            location_label="Venue", location_html="McCord Hall | ASU Tempe campus",
            date_text="October 16, 2026", time_text="8:30 AM - 12:30PM",
            registration_url="https://specialevents.asu.edu/ereg/newreg.php?eventid=1",
            presented_by=None):
    presented_by_block = ""
    if presented_by:
        presented_by_block = (
            f'<p class="detail-title">Presented By</p><p class="details-text">{presented_by}</p>'
        )
    return DETAIL_TEMPLATE.format(
        category_classes=category_classes, description=description,
        location_label=location_label, location_html=location_html,
        date_text=date_text, time_text=time_text, registration_url=registration_url,
        presented_by_block=presented_by_block,
    )


def _fetch_map(pages):
    def fetch(url, timeout):
        if url not in pages:
            raise ValueError(f"unexpected fetch: {url}")
        return pages[url]
    return fetch


class ListingParseTests(unittest.TestCase):
    def test_parses_name(self):
        page = _listing(_card(title="TiE Phoenix Monthly Event"))
        pages = {LISTING_URL: page, f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": "<div></div>"}
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["name"], "TiE Phoenix Monthly Event")

    def test_parses_listing_date_text(self):
        page = _listing(_card(date_text="Sep 18, 2026"))
        pages = {LISTING_URL: page, f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": "<div></div>"}
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["date_text"], "Sep 18, 2026")

    def test_parses_source_url(self):
        page = _listing(_card(href="https://www.aztechcouncil.org/event/foo/"))
        pages = {LISTING_URL: page, f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": "<div></div>"}
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["source_url"], "https://www.aztechcouncil.org/event/foo/")

    def test_parses_excerpt_as_description(self):
        page = _listing(_card(excerpt="Join us for an evening of networking."))
        pages = {LISTING_URL: page, f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": "<div></div>"}
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["description"], "Join us for an evening of networking.")

    def test_multiple_cards_all_parsed(self):
        page = _listing(_card(href="https://x/1", title="Event 1") + _card(href="https://x/2", title="Event 2"))
        pages = {LISTING_URL: page, f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": "<div></div>"}
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        self.assertEqual([c["name"] for c in result["candidates"]], ["Event 1", "Event 2"])

    def test_card_missing_title_is_skipped(self):
        malformed = """<a href="https://x/1" class="post-card event-card  "><div class="card-content-wrapper"><p class="card-date">Sep 1, 2026</p></div></a>"""
        page = _listing(malformed)
        pages = {LISTING_URL: page, f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": "<div></div>"}
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        self.assertEqual(result["candidates"], [])

    def test_empty_listing_returns_no_candidates(self):
        pages = {LISTING_URL: "<div class='feed-content'></div>", f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": "<div></div>"}
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["pages_fetched"], 2)  # one request per default category bucket


class DetailEnrichmentTests(unittest.TestCase):
    def _collect_one(self, detail_html, fetch_detail=True):
        listing_page = _listing(_card(href="https://www.aztechcouncil.org/event/foo/"))
        pages = {
            LISTING_URL: listing_page,
            f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": "<div></div>",
            "https://www.aztechcouncil.org/event/foo/": detail_html,
        }
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=fetch_detail)
        return result["candidates"][0]

    def test_detail_time_added(self):
        candidate = self._collect_one(_detail(time_text="8:30 AM - 12:30PM"))
        self.assertEqual(candidate["time_text"], "8:30 AM - 12:30PM")

    def test_detail_venue_used_as_location(self):
        candidate = self._collect_one(_detail(location_label="Venue", location_html="McCord Hall | ASU Tempe campus"))
        self.assertEqual(candidate["location_text"], "McCord Hall | ASU Tempe campus")

    def test_detail_location_address_joined_cleanly_when_no_venue(self):
        location_html = (
            '<span class="address">Virtual Event</span><br/>'
            '<span class="address">Register to Attend</span>'
        )
        candidate = self._collect_one(_detail(location_label="Location", location_html=location_html))
        self.assertEqual(candidate["location_text"], "Virtual Event, Register to Attend")

    def test_detail_categories_extracted(self):
        candidate = self._collect_one(_detail(category_classes="event_category-community-tech-events event_category-virtual-aztc-events"))
        self.assertEqual(candidate["categories"], ["community-tech-events", "virtual-aztc-events"])

    def test_detail_registration_url_extracted(self):
        candidate = self._collect_one(_detail(registration_url="https://specialevents.asu.edu/ereg/newreg.php?eventid=887443&amp;categoryid=1"))
        self.assertEqual(candidate["registration_url"], "https://specialevents.asu.edu/ereg/newreg.php?eventid=887443&categoryid=1")

    def test_detail_presented_by_used_as_organizer(self):
        candidate = self._collect_one(_detail(presented_by="ASU W.P. Carey School of Business"))
        self.assertEqual(candidate["organizer"], "ASU W.P. Carey School of Business")

    def test_detail_description_overrides_excerpt(self):
        candidate = self._collect_one(_detail(description="A much longer full description."))
        self.assertEqual(candidate["description"], "A much longer full description.")

    def test_detail_date_overrides_listing_date(self):
        candidate = self._collect_one(_detail(date_text="October 16, 2026"))
        self.assertEqual(candidate["date_text"], "October 16, 2026")

    def test_fetch_detail_false_skips_enrichment(self):
        candidate = self._collect_one(_detail(time_text="8:30 AM - 12:30PM"), fetch_detail=False)
        self.assertNotIn("time_text", candidate)

    def test_detail_fetch_failure_leaves_candidate_unenriched_not_raised(self):
        listing_page = _listing(_card(href="https://www.aztechcouncil.org/event/foo/"))

        def flaky_fetch(url, timeout):
            if "event/foo" in url:
                raise TimeoutError("simulated network failure")
            return listing_page if url == LISTING_URL else "<div></div>"

        result = collect_listing(fetch_func=flaky_fetch)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertNotIn("time_text", result["candidates"][0])

    def test_malformed_detail_page_does_not_crash(self):
        candidate = self._collect_one("<html><body>not the expected structure at all</body></html>")
        self.assertEqual(candidate["name"], "An Event")  # listing data survives


class PaginationAndCategoryTests(unittest.TestCase):
    def test_two_default_category_buckets_both_fetched(self):
        all_aztc_page = _listing(_card(href="https://x/1", title="AZTC Event"))
        community_page = _listing(_card(href="https://x/2", title="Community Event"))
        pages = {
            LISTING_URL: all_aztc_page,
            f"{LISTING_URL}?event_cat={CATEGORY_COMMUNITY_TECH}": community_page,
        }
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        names = {c["name"] for c in result["candidates"]}
        self.assertEqual(names, {"AZTC Event", "Community Event"})

    def test_page_repeating_same_urls_stops_pagination(self):
        # Mirrors the real site's non-JS `?paged=2` fallback, which
        # re-serves page 1's content rather than truly paginating.
        page1 = _listing(_card(href="https://x/1", title="Event 1"))
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            if CATEGORY_COMMUNITY_TECH in url:
                return "<div></div>"
            return page1

        result = collect_listing(fetch_func=fetch, fetch_detail=False, max_pages=3)
        self.assertEqual(len(result["candidates"]), 1)
        # Exactly one real page fetched for the all_aztc bucket (page 2
        # would contribute zero NEW candidates, so pagination stops there).
        all_aztc_calls = [c for c in calls if CATEGORY_COMMUNITY_TECH not in c]
        self.assertEqual(len(all_aztc_calls), 2)  # page 1 (new) + page 2 (duplicate -> stop)

    def test_custom_category_slugs_respected(self):
        page = _listing(_card(href="https://x/1", title="Optics Event"))
        url = f"{LISTING_URL}?event_cat=optics-valley"
        result = collect_listing(category_slugs=("optics-valley",), fetch_func=_fetch_map({url: page}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["name"], "Optics Event")

    def test_first_page_fetch_failure_is_isolated_per_bucket(self):
        def fetch(url, timeout):
            if CATEGORY_COMMUNITY_TECH in url:
                raise ConnectionError("simulated failure")
            return _listing(_card(href="https://x/1", title="Event 1"))

        result = collect_listing(fetch_func=fetch, fetch_detail=False)
        self.assertEqual([c["name"] for c in result["candidates"]], ["Event 1"])


if __name__ == "__main__":
    unittest.main()
