"""Facebook Marketplace: card text parsing and the two-phase fetch.

The browser is injected — a fake implementing the three methods the scraper uses
(`goto`, `cards`, `detail`). Playwright is never imported, so this file runs on a plain
`uv sync` with no Chromium installed, which is the point of guarding that import.
"""

import pytest

from apt_finder.scrapers.facebook import FacebookScraper, card_to_listing, parse_card_text


class FakeBrowser:
    """Records navigation, serves canned cards and detail text."""

    def __init__(self, cards, details=None, pages=1):
        self._cards = cards
        self._details = details or {}
        self._pages = pages
        self.visited: list[str] = []
        self.detail_calls: list[str] = []
        self.scrolls = 0

    def goto(self, url):
        self.visited.append(url)

    def cards(self):
        return self._cards

    def scroll(self):
        self.scrolls += 1

    def detail(self, url):
        self.detail_calls.append(url)
        return self._details.get(url, "")

    def close(self):
        pass


@pytest.fixture
def no_wait(settings):
    settings.throttle_min_seconds = 0
    settings.throttle_max_seconds = 0
    settings.throttle_long_pause_every = 0
    return settings


# --- card text parsing -----------------------------------------------------


def test_parses_a_typical_card():
    parsed = parse_card_text("CA$1,450\n4 1/2 rénové, laveuse sécheuse\nGatineau, QC")
    assert parsed["price_text"] == "CA$1,450"
    assert parsed["title"] == "4 1/2 rénové, laveuse sécheuse"
    assert parsed["location_text"] == "Gatineau, QC"


def test_drops_decoration_lines():
    parsed = parse_card_text("Publié il y a 3 jours\nCA$1,450\nGrand 4 1/2\nGatineau, QC")
    assert parsed["title"] == "Grand 4 1/2"


def test_card_without_a_price():
    parsed = parse_card_text("Appartement à louer\nGatineau, QC")
    assert parsed["price_text"] is None
    assert parsed["location_text"] == "Gatineau, QC"


def test_card_with_only_a_title():
    parsed = parse_card_text("Beau logement")
    assert parsed["title"] == "Beau logement"
    assert parsed["location_text"] is None


def test_empty_card():
    assert parse_card_text("") == {"price_text": None, "title": None, "location_text": None}


def test_long_last_line_is_not_treated_as_a_location():
    text = "CA$1,450\nCourt\n" + "Une description beaucoup trop longue pour être une ville" * 2
    assert parse_card_text(text)["location_text"] is None


def test_card_to_listing():
    listing = card_to_listing(
        {
            "id": "987",
            "href": "https://www.facebook.com/marketplace/item/987",
            "text": "CA$1,450\nGrand 4 1/2\nGatineau, QC",
        }
    )
    assert listing.platform == "facebook"
    assert listing.external_id == "987"
    assert listing.description is None  # phase 2 fills this in


def test_card_without_an_id_is_skipped():
    assert card_to_listing({"href": "x", "text": "y"}) is None


# --- the pre-filter that decides whether to spend a page load --------------


def test_over_budget_card_is_not_opened(no_wait):
    scraper = FacebookScraper(config=no_wait, browser=FakeBrowser([]))
    listing = card_to_listing(
        {"id": "1", "href": "u", "text": "CA$3,200\nGrand 6 1/2\nGatineau, QC"}
    )
    assert scraper.is_worth_opening(listing) is False


def test_ontario_card_is_not_opened(no_wait):
    scraper = FacebookScraper(config=no_wait, browser=FakeBrowser([]))
    listing = card_to_listing({"id": "1", "href": "u", "text": "CA$1,450\nNice 2 bed\nOttawa, ON"})
    assert scraper.is_worth_opening(listing) is False


def test_in_budget_quebec_card_is_opened(no_wait):
    scraper = FacebookScraper(config=no_wait, browser=FakeBrowser([]))
    listing = card_to_listing(
        {"id": "1", "href": "u", "text": "CA$1,450\nGrand 4 1/2\nGatineau, QC"}
    )
    assert scraper.is_worth_opening(listing) is True


def test_card_missing_information_is_still_opened(no_wait):
    # Only contradictions disqualify. A card with no price or city is exactly the case
    # the detail page exists to resolve.
    scraper = FacebookScraper(config=no_wait, browser=FakeBrowser([]))
    listing = card_to_listing({"id": "1", "href": "u", "text": "Appartement à louer"})
    assert scraper.is_worth_opening(listing) is True


# --- the two-phase collect -------------------------------------------------


def test_detail_pages_are_fetched_only_for_survivors(no_wait):
    cards = [
        {"id": "1", "href": "u1", "text": "CA$1,450\nGrand 4 1/2\nGatineau, QC"},
        {"id": "2", "href": "u2", "text": "CA$3,900\nPenthouse\nGatineau, QC"},
        {"id": "3", "href": "u3", "text": "CA$1,300\nNice flat\nOttawa, ON"},
    ]
    browser = FakeBrowser(cards, details={"u1": "Laveuse-sécheuse incluses"})
    listings, stopped = FacebookScraper(config=no_wait, browser=browser).collect()

    # This is the whole ban-avoidance argument: 3 cards, 1 detail load.
    assert browser.detail_calls == ["u1"]
    assert stopped is None
    assert len(listings) == 3


def test_detail_text_lands_on_the_listing(no_wait):
    cards = [{"id": "1", "href": "u1", "text": "CA$1,450\nGrand 4 1/2\nGatineau, QC"}]
    browser = FakeBrowser(cards, details={"u1": "Laveuse-sécheuse incluses, frigo, cuisinière"})
    listings, _ = FacebookScraper(config=no_wait, browser=browser).collect()
    assert "Laveuse" in listings[0].description
    assert "detail_text" in listings[0].raw


def test_scrolling_stops_when_the_grid_stops_growing(no_wait):
    cards = [{"id": "1", "href": "u1", "text": "CA$1,450\nGrand\nGatineau, QC"}]
    browser = FakeBrowser(cards)
    FacebookScraper(config=no_wait, browser=browser).collect()
    # One productive pass, then one that adds nothing, then stop.
    assert browser.scrolls <= 2


def test_listing_cap_stops_the_scrolling(no_wait):
    # The cap bounds how far we scroll, not what a single pass harvests: once the grid
    # has handed over more than the cap, we stop asking it for more.
    cards = [
        {"id": str(i), "href": f"u{i}", "text": "CA$1,450\nGrand\nGatineau, QC"} for i in range(10)
    ]
    no_wait.facebook_max_listings = 2
    capped = FakeBrowser(cards)
    FacebookScraper(config=no_wait, browser=capped).collect()

    no_wait.facebook_max_listings = 60
    uncapped = FakeBrowser(cards)
    FacebookScraper(config=no_wait, browser=uncapped).collect()

    assert capped.scrolls < uncapped.scrolls


def test_budget_exhaustion_returns_what_was_collected(no_wait):
    no_wait.throttle_max_requests_per_run = 1
    cards = [{"id": "1", "href": "u1", "text": "CA$1,450\nGrand\nGatineau, QC"}]
    listings, stopped = FacebookScraper(config=no_wait, browser=FakeBrowser(cards)).collect()
    assert stopped is not None
    assert len(listings) == 1


def test_empty_detail_leaves_the_card_intact(no_wait):
    cards = [{"id": "1", "href": "u1", "text": "CA$1,450\nGrand 4 1/2\nGatineau, QC"}]
    listings, _ = FacebookScraper(config=no_wait, browser=FakeBrowser(cards, details={})).collect()
    assert listings[0].description is None
    assert listings[0].title == "Grand 4 1/2"


def test_module_imports_without_playwright():
    # The guarded import is what keeps a default `uv sync` (no browser) usable for the
    # Kijiji path and lets this whole file run.
    import apt_finder.scrapers.facebook as module

    assert module.PLAYWRIGHT_HINT
