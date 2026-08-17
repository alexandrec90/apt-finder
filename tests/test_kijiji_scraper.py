"""Kijiji parsing and pagination, over fixture HTML.

No socket is opened: the HTTP client is injected. The JSON shapes below mirror what
Kijiji's `__NEXT_DATA__` actually carries, including the parts that vary (a price as a
label vs. as integer cents, a location as a string vs. a nested object).
"""

import json

import pytest

from apt_finder.scrapers.kijiji import (
    KijijiScraper,
    extract_next_data,
    iter_listing_nodes,
    node_to_listing,
    page_url,
    parse_search_page,
)


def wrap(payload: dict) -> str:
    """A page carrying `payload` in its __NEXT_DATA__ script tag."""
    return (
        "<html><body><div>ads</div>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )


LISTING = {
    "id": "1700123456",
    "title": "Beau 4 1/2 à Gatineau",
    "description": "Laveuse-sécheuse incluses",
    "price": {"amount": 145000, "currency": "CAD"},
    "seoUrl": "/v-appartement-condo/gatineau/beau-4-1-2/1700123456",
    "location": {"name": "Gatineau, QC", "coordinates": {"latitude": 45.47, "longitude": -75.70}},
}


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


class FakeClient:
    """Serves canned responses in order and records the URLs requested."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requested: list[str] = []

    def get(self, url, headers=None):
        self.requested.append(url)
        return self._responses.pop(0) if self._responses else FakeResponse(wrap({}), 200)

    def close(self):
        pass


# --- __NEXT_DATA__ extraction ---------------------------------------------


def test_extracts_next_data():
    assert extract_next_data(wrap({"a": 1})) == {"a": 1}


def test_missing_next_data_returns_none():
    assert extract_next_data("<html><body>no script</body></html>") is None


def test_malformed_next_data_returns_none():
    html = '<script id="__NEXT_DATA__" type="application/json">{not json</script>'
    assert extract_next_data(html) is None


# --- structural node walking ----------------------------------------------


def test_finds_listings_at_any_depth():
    # Kijiji has moved the results array between several paths; the walker recognises
    # the shape instead of following a path, so a move does not break parsing.
    payload = {"props": {"pageProps": {"searchResults": {"results": [LISTING]}}}}
    assert [node["id"] for node in iter_listing_nodes(payload)] == ["1700123456"]


def test_finds_listings_under_a_different_path():
    payload = {"deeply": {"nested": [{"other": [LISTING]}]}}
    assert len(list(iter_listing_nodes(payload))) == 1


def test_ignores_non_listing_dicts():
    payload = {"user": {"id": "9", "name": "x"}, "config": {"id": "1"}}
    assert list(iter_listing_nodes(payload)) == []


def test_each_listing_yielded_once():
    shared = dict(LISTING)
    payload = {"a": [shared], "b": [shared]}
    assert len(list(iter_listing_nodes(payload))) == 1


# --- node -> ScrapedListing ------------------------------------------------


def test_maps_a_full_node():
    listing = node_to_listing(LISTING)
    assert listing.platform == "kijiji"
    assert listing.external_id == "1700123456"
    assert listing.title == "Beau 4 1/2 à Gatineau"
    assert listing.url.startswith("https://www.kijiji.ca/v-appartement-condo/")
    assert listing.location_text == "Gatineau, QC"
    assert listing.latitude == 45.47
    assert listing.raw == LISTING


def test_integer_cents_become_dollars():
    # Kijiji quotes money in integer cents; reading it as dollars is a 100x error.
    assert node_to_listing(LISTING).price_text == "1450.00 $"


def test_small_integer_price_is_taken_as_dollars():
    node = {**LISTING, "price": {"amount": 1450}}
    assert node_to_listing(node).price_text == "1450.00 $"


def test_price_label_is_preferred_over_the_numeric_field():
    # The label is what a human saw, so it carries the period the number lacks.
    node = {**LISTING, "price": {"amount": 145000, "label": "1 450 $ / mois"}}
    assert node_to_listing(node).price_text == "1 450 $ / mois"


def test_plain_string_price():
    assert node_to_listing({**LISTING, "price": "1 450 $"}).price_text == "1 450 $"


def test_location_as_a_plain_string():
    listing = node_to_listing({**LISTING, "location": "Aylmer, QC"})
    assert listing.location_text == "Aylmer, QC"


def test_postal_code_is_appended_to_the_location():
    # The strongest province signal we have, so it must reach `locate()`.
    node = {**LISTING, "location": {"name": "Gatineau", "postalCode": "J8Y 3X5"}}
    assert "J8Y 3X5" in node_to_listing(node).location_text


def test_attributes_list_is_flattened():
    node = {**LISTING, "attributes": [{"name": "Laveuse", "value": "Oui"}]}
    assert node_to_listing(node).attributes == {"Laveuse": "Oui"}


def test_attributes_dict_passes_through():
    node = {**LISTING, "attributes": {"Laveuse": "Oui"}}
    assert node_to_listing(node).attributes == {"Laveuse": "Oui"}


def test_posted_at_parsed_and_made_aware():
    node = {**LISTING, "activationDate": "2026-08-01T12:00:00Z"}
    posted = node_to_listing(node).posted_at
    assert posted.year == 2026
    assert posted.tzinfo is not None


def test_bad_date_is_ignored_not_fatal():
    assert node_to_listing({**LISTING, "activationDate": "yesterday"}).posted_at is None


def test_node_without_an_id_is_skipped():
    assert node_to_listing({"title": "x", "price": "1 $"}) is None


def test_missing_optional_fields_do_not_crash():
    listing = node_to_listing({"id": "5", "title": "t", "url": "/x"})
    assert listing.external_id == "5"
    assert listing.location_text is None
    assert listing.latitude is None


# --- pagination ------------------------------------------------------------


def test_page_url_first_page_unchanged():
    base = "https://www.kijiji.ca/b-appartement-condo/gatineau/c37l1700242"
    assert page_url(base, 1) == base


def test_page_url_inserts_the_page_segment():
    base = "https://www.kijiji.ca/b-appartement-condo/gatineau/c37l1700242"
    assert page_url(base, 3) == (
        "https://www.kijiji.ca/b-appartement-condo/gatineau/page-3/c37l1700242"
    )


# --- the scraper itself ----------------------------------------------------


@pytest.fixture
def no_wait(settings):
    """Zero delays, so scraper tests measure logic rather than patience."""
    settings.throttle_min_seconds = 0
    settings.throttle_max_seconds = 0
    settings.throttle_long_pause_every = 0
    return settings


def test_collects_across_pages(no_wait):
    no_wait.kijiji_max_pages = 2
    second = {**LISTING, "id": "222"}
    client = FakeClient([FakeResponse(wrap({"r": [LISTING]})), FakeResponse(wrap({"r": [second]}))])
    scraper = KijijiScraper(config=no_wait, client=client)

    listings, stopped = scraper.collect()

    assert {item.external_id for item in listings} == {"1700123456", "222"}
    assert stopped is None
    assert len(client.requested) == 2


def test_stops_at_the_first_empty_page(no_wait):
    # An empty page is the end of pagination, not an error.
    no_wait.kijiji_max_pages = 5
    client = FakeClient([FakeResponse(wrap({"r": [LISTING]})), FakeResponse(wrap({}))])
    listings, stopped = KijijiScraper(config=no_wait, client=client).collect()
    assert len(listings) == 1
    assert stopped is None
    assert len(client.requested) == 2


def test_duplicate_ids_are_collapsed(no_wait):
    no_wait.kijiji_max_pages = 2
    client = FakeClient(
        [FakeResponse(wrap({"r": [LISTING]})), FakeResponse(wrap({"r": [LISTING]}))]
    )
    listings, _ = KijijiScraper(config=no_wait, client=client).collect()
    assert len(listings) == 1


def test_429_trips_the_breaker_and_keeps_what_was_collected(no_wait):
    no_wait.kijiji_max_pages = 4
    no_wait.throttle_block_threshold = 1
    client = FakeClient([FakeResponse(wrap({"r": [LISTING]})), FakeResponse("", 429)])
    listings, stopped = KijijiScraper(config=no_wait, client=client).collect()
    # A partial sweep beats a banned account — the first page survives.
    assert len(listings) == 1
    assert stopped is not None


def test_request_budget_stops_the_run(no_wait):
    no_wait.kijiji_max_pages = 10
    no_wait.throttle_max_requests_per_run = 2
    client = FakeClient([FakeResponse(wrap({"r": [LISTING]}))] * 10)
    listings, stopped = KijijiScraper(config=no_wait, client=client).collect()
    assert "cap" in stopped
    assert len(client.requested) == 2
    # Whatever the budget bought is still returned, not discarded.
    assert len(listings) == 1


def test_unexpected_status_stops_that_url(no_wait):
    client = FakeClient([FakeResponse("", 500)])
    listings, stopped = KijijiScraper(config=no_wait, client=client).collect()
    assert listings == []
    assert "500" in stopped


def test_french_is_requested_explicitly(no_wait):
    scraper = KijijiScraper(config=no_wait, client=FakeClient([]))
    assert scraper._headers()["Accept-Language"].startswith("fr-CA")


def test_cookie_header_sent_when_configured(no_wait):
    no_wait.kijiji_cookie = "session=abc"
    scraper = KijijiScraper(config=no_wait, client=FakeClient([]))
    assert scraper._headers()["Cookie"] == "session=abc"


def test_no_cookie_header_when_unset(no_wait):
    assert "Cookie" not in KijijiScraper(config=no_wait, client=FakeClient([]))._headers()


def test_parse_search_page_on_a_page_without_next_data():
    assert parse_search_page("<html></html>") == []
