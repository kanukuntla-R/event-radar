"""Tests for asu_career_collector.collect_listing(). Fully offline: fetch is
always injected, no real network calls. Stdlib unittest only.

Run with: python -m unittest test_asu_career_collector -v
"""
import unittest

from asu_career_collector import collect_listing

BASE_URL = "https://career.eoss.asu.edu/events/"

ITEM_TEMPLATE = """
<li class="event_item post type-event">
    <a href="{href}">
        <div class="day_box" aria-hidden="true">
            <span class="day_box_month">Sep</span>
            <span class="day_box_date">08</span>
        </div>
        <div class="text-content">
            <h3 class="title">
                <span class="screen-reader-text">Event: </span>{title}
            </h3>
            <div>
                <p class="event-date">
                    {date_text}
                </p>
                <p class="event-time">
                    {time_text}
                </p>
                <p class="event-date-time-short">short</p>
                <p class="event-location">
                    {location_text}
                </p>
            </div>
        </div>
    </a>
</li>
"""


def _listing_page(items_html, next_page_links=""):
    return f"""
    <div class="pagination top" role="navigation" aria-label="Pagination">
        {next_page_links}
    </div>
    <ul id="events-list" class="events-list">
        {items_html}
    </ul>
    """


def _page_link(page_url, page_num):
    return f'<a class="page-numbers" href="{page_url}"><span class="screen-reader-text">Page</span> {page_num}</a>'


def _fetch_map(pages):
    def fetch(url, timeout):
        if url not in pages:
            raise ValueError(f"unexpected fetch: {url}")
        return pages[url]
    return fetch


class ListingParseTests(unittest.TestCase):
    def test_parses_event_name_strips_prefix(self):
        item = ITEM_TEMPLATE.format(
            href=BASE_URL + "2026/09/08/foo/", title="Career Fair 2026",
            date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST",
            location_text="Virtual",
        )
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map({BASE_URL: _listing_page(item)}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["name"], "Career Fair 2026")

    def test_parses_one_day_date(self):
        item = ITEM_TEMPLATE.format(
            href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026",
            time_text="10am - 11am MST", location_text="Virtual",
        )
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map({BASE_URL: _listing_page(item)}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["date_text"], "Tuesday, September 8, 2026")

    def test_parses_date_range(self):
        item = ITEM_TEMPLATE.format(
            href=BASE_URL + "a/", title="A",
            date_text="Monday, August 17, 2026 - Monday, December 21, 2026",
            time_text="12pm - 1pm MST", location_text="Virtual",
        )
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map({BASE_URL: _listing_page(item)}), fetch_detail=False)
        self.assertEqual(
            result["candidates"][0]["date_text"], "Monday, August 17, 2026 - Monday, December 21, 2026"
        )

    def test_parses_start_end_time(self):
        item = ITEM_TEMPLATE.format(
            href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026",
            time_text="10am - 10:30am MST", location_text="Virtual",
        )
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map({BASE_URL: _listing_page(item)}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["time_text"], "10am - 10:30am MST")

    def test_parses_virtual_location(self):
        item = ITEM_TEMPLATE.format(
            href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026",
            time_text="10am - 11am MST", location_text="Virtual",
        )
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map({BASE_URL: _listing_page(item)}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["location_text"], "Virtual")

    def test_parses_tempe_address(self):
        item = ITEM_TEMPLATE.format(
            href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026",
            time_text="10am - 11am MST", location_text="Tempe, ECG, North Patio",
        )
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map({BASE_URL: _listing_page(item)}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["location_text"], "Tempe, ECG, North Patio")

    def test_extracts_event_detail_link(self):
        href = BASE_URL + "2026/09/08/resume-workshop/"
        item = ITEM_TEMPLATE.format(
            href=href, title="A", date_text="Tuesday, September 8, 2026",
            time_text="10am - 11am MST", location_text="Virtual",
        )
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map({BASE_URL: _listing_page(item)}), fetch_detail=False)
        self.assertEqual(result["candidates"][0]["source_url"], href)


class PaginationTests(unittest.TestCase):
    def test_bounded_pagination_stops_at_max_pages(self):
        page1_url, page2_url, page3_url, page4_url = (BASE_URL, BASE_URL + "page/2/", BASE_URL + "page/3/", BASE_URL + "page/4/")
        item = ITEM_TEMPLATE.format(href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")
        pages = {
            page1_url: _listing_page(item, _page_link(page2_url, 2)),
            page2_url: _listing_page(item, _page_link(page3_url, 3)),
            page3_url: _listing_page(item, _page_link(page4_url, 4)),
            page4_url: _listing_page(item),
        }
        result = collect_listing(url=page1_url, fetch_func=_fetch_map(pages), max_pages=2, fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(len(result["candidates"]), 2)

    def test_stops_when_no_next_page(self):
        item = ITEM_TEMPLATE.format(href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")
        pages = {BASE_URL: _listing_page(item)}  # no page-numbers links at all
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map(pages), max_pages=5, fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 1)

    def test_repeated_page_stops(self):
        page2_url = BASE_URL + "page/2/"
        item = ITEM_TEMPLATE.format(href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")
        pages = {
            BASE_URL: _listing_page(item, _page_link(page2_url, 2)),
            # page 2 links back to page 1 (current URL) instead of forward
            page2_url: _listing_page(item, _page_link(BASE_URL, 1)),
        }
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map(pages), max_pages=10, fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 2)

    def test_reports_pages_fetched(self):
        item = ITEM_TEMPLATE.format(href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")
        result = collect_listing(url=BASE_URL, fetch_func=_fetch_map({BASE_URL: _listing_page(item)}), fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 1)


class NetworkErrorTests(unittest.TestCase):
    def test_first_page_error_returns_empty_not_raise(self):
        def fetch(url, timeout):
            raise OSError("unreachable")
        result = collect_listing(url=BASE_URL, fetch_func=fetch, fetch_detail=False)
        self.assertEqual(result, {"candidates": [], "pages_fetched": 0})

    def test_second_page_error_keeps_first_page_results(self):
        page2_url = BASE_URL + "page/2/"
        item = ITEM_TEMPLATE.format(href=BASE_URL + "a/", title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")

        def fetch(url, timeout):
            if url == BASE_URL:
                return _listing_page(item, _page_link(page2_url, 2))
            raise OSError("page 2 unreachable")

        result = collect_listing(url=BASE_URL, fetch_func=fetch, max_pages=5, fetch_detail=False)
        self.assertEqual(result["pages_fetched"], 1)
        self.assertEqual(len(result["candidates"]), 1)


class DetailFetchTests(unittest.TestCase):
    def test_detail_page_enriches_description_and_registration_url(self):
        href = BASE_URL + "a/"
        item = ITEM_TEMPLATE.format(href=href, title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")
        detail_html = """
        <div class="entry-content"><p>Learn resume basics.</p></div>
        <a class="button rsvp-button" href="https://career-asu.12twenty.com/x">Click here to attend</a>
        """
        result = collect_listing(
            url=BASE_URL,
            fetch_func=_fetch_map({BASE_URL: _listing_page(item), href: detail_html}),
        )
        candidate = result["candidates"][0]
        self.assertIn("Learn resume basics.", candidate["description"])
        self.assertEqual(candidate["registration_url"], "https://career-asu.12twenty.com/x")

    def test_detail_fetch_failure_does_not_crash(self):
        href = BASE_URL + "a/"
        item = ITEM_TEMPLATE.format(href=href, title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")

        def fetch(url, timeout):
            if url == BASE_URL:
                return _listing_page(item)
            raise OSError("detail unreachable")

        result = collect_listing(url=BASE_URL, fetch_func=fetch)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertNotIn("description", result["candidates"][0])

    def test_no_recursive_crawling_only_one_detail_fetch_per_candidate(self):
        href = BASE_URL + "a/"
        item = ITEM_TEMPLATE.format(href=href, title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")
        detail_html = """
        <div class="entry-content"><p>Description with <a href="https://unrelated.example.com/other">another link</a> inside.</p></div>
        """
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            if url == BASE_URL:
                return _listing_page(item)
            if url == href:
                return detail_html
            raise AssertionError(f"should never fetch {url}")

        collect_listing(url=BASE_URL, fetch_func=fetch)
        self.assertEqual(calls, [BASE_URL, href])
        self.assertNotIn("https://unrelated.example.com/other", calls)

    def test_fetch_detail_disabled_skips_extra_requests(self):
        href = BASE_URL + "a/"
        item = ITEM_TEMPLATE.format(href=href, title="A", date_text="Tuesday, September 8, 2026", time_text="10am - 11am MST", location_text="Virtual")
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return _listing_page(item)

        collect_listing(url=BASE_URL, fetch_func=fetch, fetch_detail=False)
        self.assertEqual(calls, [BASE_URL])


if __name__ == "__main__":
    unittest.main()
