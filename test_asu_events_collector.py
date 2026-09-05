"""Tests for asu_events_collector.collect_listing(). Fully offline: fetch is
always injected, no real network calls. Fixtures mirror the real
asuevents.asu.edu Drupal-Views markup observed live. Stdlib unittest only.

Run with: python -m unittest test_asu_events_collector -v
"""
import unittest

from asu_events_collector import collect_listing, CATEGORY_PROFESSIONAL_AND_CAREER_DEVELOPMENT, CATEGORY_SCIENCE

BASE_URL = "https://asuevents.asu.edu"

CARD_TEMPLATE = """
<li class="card cards-components card-event"><div class="views-field views-field-field-asu-events-event-image"><div class="field-content"><a href="{href}"><img src="x.png" /></a></div></div><div class="views-field views-field-nothing card-header"><h3 class="field-content card-title"><a href="{href}">{title}</a>
</h3></div><div class="views-field views-field-field-event-date-value-1"><span class="field-content">  <i class="far fa-calendar"></i>
{date_text}</span></div><div class="views-field views-field-field-event-date-end-value"><span class="field-content">




<span class="smart-date--time">{start_time}</span> &ndash; <span class="smart-date--time">{end_time}</span>




</span></div><div class="views-field views-field-field-asu-events-location card-event-details"><div class="field-content"><i class="fas fa-map-marker-alt"></i>&nbsp;&nbsp;{location}</div></div></li>
"""


def _card(href="/event/foo?eventDate=2026-09-08&amp;id=0", title="An Event",
          date_text="Tue, Sep 8, 2026", start_time="10:00 am", end_time="11:00 am (MST)",
          location="Online event"):
    return CARD_TEMPLATE.format(href=href, title=title, date_text=date_text, start_time=start_time, end_time=end_time, location=location)


def _listing_page(cards_html):
    return f'<ul class="events-card-grid">{cards_html}</ul>'


def _fetch_map(pages):
    def fetch(url, timeout):
        if url not in pages:
            raise ValueError(f"unexpected fetch: {url}")
        return pages[url]
    return fetch


def _page_url(page, category_ids=(CATEGORY_PROFESSIONAL_AND_CAREER_DEVELOPMENT, CATEGORY_SCIENCE)):
    params = "&".join(f"category%5B{cid}%5D={cid}" for cid in category_ids)
    return f"{BASE_URL}/?{params}&page={page}"


class ListingParseTests(unittest.TestCase):
    def test_parses_name(self):
        page0 = _listing_page(_card(title="KE Analytics Open Office Hours"))
        result = collect_listing(fetch_func=_fetch_map({_page_url(0): page0, _page_url(1): "<ul></ul>"}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["name"], "KE Analytics Open Office Hours")

    def test_parses_date(self):
        page0 = _listing_page(_card(date_text="Tue, Sep 8, 2026"))
        result = collect_listing(fetch_func=_fetch_map({_page_url(0): page0, _page_url(1): "<ul></ul>"}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["date_text"], "Tue, Sep 8, 2026")

    def test_parses_time(self):
        page0 = _listing_page(_card(start_time="1:00 pm", end_time="3:30 pm (MST)"))
        result = collect_listing(fetch_func=_fetch_map({_page_url(0): page0, _page_url(1): "<ul></ul>"}), fetch_detail=False)
        self.assertIn("1:00 pm", result["candidates"][0]["time_text"])
        self.assertIn("3:30 pm", result["candidates"][0]["time_text"])

    def test_parses_location(self):
        # raw collector evidence still carries HTML entities (e.g. &nbsp;) -
        # unescaping happens downstream in the normalizer, same contract as
        # asu_career_collector.py.
        page0 = _listing_page(_card(location="Online event"))
        result = collect_listing(fetch_func=_fetch_map({_page_url(0): page0, _page_url(1): "<ul></ul>"}), fetch_detail=False)
        self.assertIn("Online event", result["candidates"][0]["location_text"])

    def test_source_url_unescapes_ampersand_and_resolves_relative_href(self):
        page0 = _listing_page(_card(href="/event/foo?eventDate=2026-09-08&amp;id=26"))
        result = collect_listing(fetch_func=_fetch_map({_page_url(0): page0, _page_url(1): "<ul></ul>"}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["source_url"], f"{BASE_URL}/event/foo?eventDate=2026-09-08&id=26")

    def test_multiple_cards_on_one_page(self):
        page0 = _listing_page(_card(title="First") + _card(title="Second", href="/event/second"))
        result = collect_listing(fetch_func=_fetch_map({_page_url(0): page0, _page_url(1): "<ul></ul>"}), fetch_detail=False)
        self.assertEqual([c["name"] for c in result["candidates"]], ["First", "Second"])


class PaginationTests(unittest.TestCase):
    def test_bounded_by_max_pages(self):
        pages = {_page_url(p): _listing_page(_card(href=f"/event/{p}")) for p in range(5)}
        result = collect_listing(fetch_func=_fetch_map(pages), max_pages=2, fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(len(result["candidates"]), 2)

    def test_stops_on_empty_page(self):
        pages = {
            _page_url(0): _listing_page(_card(href="/event/0")),
            _page_url(1): "<ul class=\"events-card-grid\"></ul>",
        }
        result = collect_listing(fetch_func=_fetch_map(pages), max_pages=5, fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(len(result["candidates"]), 1)

    def test_reports_pages_fetched(self):
        pages = {_page_url(0): _listing_page(_card()), _page_url(1): "<ul></ul>"}
        result = collect_listing(fetch_func=_fetch_map(pages), fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 2)

    def test_uses_configured_category_ids(self):
        custom_ids = (9999,)
        url = f"{BASE_URL}/?category%5B9999%5D=9999&page=0"
        result = collect_listing(category_ids=custom_ids, fetch_func=_fetch_map({url: "<ul></ul>"}), fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 1)
        self.assertEqual(result["candidates"], [])


class NetworkErrorTests(unittest.TestCase):
    def test_first_page_error_returns_empty_not_raise(self):
        def fetch(url, timeout):
            raise OSError("unreachable")
        result = collect_listing(fetch_func=fetch, fetch_detail=False)
        self.assertEqual(result, {"candidates": [], "pages_fetched": 0})

    def test_second_page_error_keeps_first_page_results(self):
        def fetch(url, timeout):
            if url == _page_url(0):
                return _listing_page(_card())
            raise OSError("page 1 unreachable")
        result = collect_listing(fetch_func=fetch, max_pages=5, fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 1)
        self.assertEqual(len(result["candidates"]), 1)


class DetailFetchTests(unittest.TestCase):
    def _detail_html(self, categories=("Professional and career development",), description="Some description.",
                      cta_url="https://asu.zoom.us/my/x", location="Zoom "):
        pills = "".join(f"<li>{c}</li>" for c in categories)
        return f"""
        <div class="views-field views-field-term-node-tid"><span class="field-content event-pills-detail"><ul>{pills}</ul></span></div>
        <div class="views-field views-field-body"><div class="field-content"><p>{description}</p></div></div>
        <div class="views-field views-field-field-event-location-title event-location"><div class="field-content"><div class="event-location-label">Location</div>
        <h2>{location}</h2></div></div>
        <div class="views-field views-field-field-call-to-action-button-text"><div class="field-content event-cta"><a href="{cta_url}" class="btn btn-gold">Join</a></div></div>
        """

    def test_enriches_categories_description_registration_and_location(self):
        href = "/event/foo"
        page0 = _listing_page(_card(href=href))
        detail = self._detail_html()
        result = collect_listing(fetch_func=_fetch_map({
            _page_url(0): page0, _page_url(1): "<ul></ul>",
            f"{BASE_URL}{href}": detail,
        }))
        candidate = result["candidates"][0]
        self.assertEqual(candidate["categories"], ["Professional and career development"])
        self.assertIn("Some description.", candidate["description"])
        self.assertEqual(candidate["registration_url"], "https://asu.zoom.us/my/x")
        self.assertEqual(candidate["location_text"], "Zoom")  # detail location has no entities to unescape

    def test_registration_url_unescapes_ampersand(self):
        href = "/event/foo"
        page0 = _listing_page(_card(href=href))
        detail = self._detail_html(cta_url="https://x.example.com/a?x=1&amp;y=2")
        result = collect_listing(fetch_func=_fetch_map({
            _page_url(0): page0, _page_url(1): "<ul></ul>", f"{BASE_URL}{href}": detail,
        }))
        self.assertEqual(result["candidates"][0]["registration_url"], "https://x.example.com/a?x=1&y=2")

    def test_detail_fetch_failure_does_not_crash(self):
        href = "/event/foo"
        page0 = _listing_page(_card(href=href))

        def fetch(url, timeout):
            if url == _page_url(0):
                return page0
            if url == _page_url(1):
                return "<ul></ul>"
            raise OSError("detail unreachable")

        result = collect_listing(fetch_func=fetch)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertNotIn("categories", result["candidates"][0])

    def test_at_most_one_detail_fetch_per_candidate_no_recursive_crawl(self):
        href = "/event/foo"
        page0 = _listing_page(_card(href=href))
        detail = self._detail_html(description='Great talk, see <a href="https://unrelated.example.com/other">more</a> here.')
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            if url == _page_url(0):
                return page0
            if url == _page_url(1):
                return "<ul></ul>"
            if url == f"{BASE_URL}{href}":
                return detail
            raise AssertionError(f"should never fetch {url}")

        collect_listing(fetch_func=fetch)
        self.assertEqual(calls.count(f"{BASE_URL}{href}"), 1)
        self.assertNotIn("https://unrelated.example.com/other", calls)

    def test_fetch_detail_disabled_skips_extra_requests(self):
        href = "/event/foo"
        page0 = _listing_page(_card(href=href))
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            if url == _page_url(0):
                return page0
            return "<ul></ul>"

        collect_listing(fetch_func=fetch, fetch_detail=False)
        self.assertNotIn(f"{BASE_URL}{href}", calls)


if __name__ == "__main__":
    unittest.main()
