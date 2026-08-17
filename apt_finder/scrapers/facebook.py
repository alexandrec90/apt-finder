"""Facebook Marketplace — a real browser, a persistent profile, and as few page loads as possible.

Marketplace has no usable HTML for an HTTP client: the markup is obfuscated, the data
arrives over authenticated GraphQL, and the whole surface is behind a login. So this
one drives a real Chromium through Playwright.

**Login is interactive and happens once.** ``apt-finder facebook-login`` opens a headed
browser; you sign in yourself, including any 2FA, and Playwright keeps the cookies in
``Settings.facebook_profile_dir``. Every later run reuses that profile headlessly. This
project never asks for, receives, or stores your password — which is both safer and
much less likely to trip a login-anomaly check than automating the login form.

**Two phases, because page loads are the currency of getting banned.** Search-result
cards carry a title, a price and a city, but not the description — and the description
is where the laundry is. Opening every result to find out would mean ~100 page loads
per sweep. Instead:

1. Read the cards from the results grid, scrolling gently.
2. Pre-filter on what the card *does* show: price against budget, city against the
   province rules. This throws away most of the grid, including all of Ottawa.
3. Only then open the survivors' detail pages for the description.

Typically that turns 100 potential loads into 10-15. Same result set, an order of
magnitude less exposure.

**Selector drift.** Facebook's class names are generated and change constantly. The one
durable anchor is the URL shape ``/marketplace/item/<id>``, so extraction keys off that
and reads the card's rendered text rather than its structure. Card text parsing is a
pure function (:func:`parse_card_text`) and fully tested. Run ``apt-finder inspect
facebook`` if results ever go empty — it dumps what the DOM actually yielded to
``logs/``.
"""

import json
import re
from dataclasses import replace
from typing import Any

from apt_finder.config import Settings
from apt_finder.listing import ScrapedListing
from apt_finder.normalize.geo import locate
from apt_finder.normalize.price import parse_price
from apt_finder.normalize.text import fold
from apt_finder.scrapers.base import ListingScraper
from apt_finder.scrapers.throttle import BlockedError, ThrottleExhaustedError

__all__ = ["PLAYWRIGHT_HINT", "FacebookScraper", "facebook_login", "parse_card_text"]

PLAYWRIGHT_HINT = (
    "Facebook scraping needs Playwright: `uv sync --extra facebook` then "
    "`uv run playwright install chromium`."
)

_ITEM_ID_RE = re.compile(r"/marketplace/item/(\d+)")

#: Reads every `/marketplace/item/` anchor in the grid: its id, href and rendered text.
#: Text, not structure — the text survives the class-name churn that breaks selectors.
_CARD_SCRIPT = """
() => {
  const out = {};
  for (const a of document.querySelectorAll('a[href*="/marketplace/item/"]')) {
    const m = a.getAttribute('href').match(/\\/marketplace\\/item\\/(\\d+)/);
    if (!m) continue;
    const text = (a.innerText || '').trim();
    if (!text) continue;
    // Same item can appear in several anchors; keep the richest one.
    if (!out[m[1]] || out[m[1]].text.length < text.length) {
      out[m[1]] = {id: m[1], href: a.href.split('?')[0], text: text};
    }
  }
  return Object.values(out);
}
"""

#: On a detail page, prefer the block under the "Description"/"Détails" heading; fall
#: back to the main region. Never the whole body — the sidebar is full of *other*
#: listings, and their amenities would be read as this one's.
_DETAIL_SCRIPT = """
() => {
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,span,div'));
  const wanted = /^(description|détails|details|description du vendeur)$/i;
  for (const h of headings) {
    const label = (h.innerText || '').trim();
    if (wanted.test(label)) {
      const host = h.parentElement && h.parentElement.parentElement;
      if (host) {
        const text = (host.innerText || '').trim();
        if (text.length > label.length + 20) return text;
      }
    }
  }
  const main = document.querySelector('[role="main"]');
  return main ? (main.innerText || '').trim().slice(0, 4000) : '';
}
"""


def parse_card_text(text: str) -> dict[str, str | None]:
    """Split a Marketplace card's rendered text into price / title / location.

    Cards render as a few short lines, roughly::

        CA$1,450
        4 1/2 tout inclus, laveuse sécheuse
        Gatineau, QC

    The price line is the one carrying a currency marker, the location is the last line
    (cities are short and comma-separated), and the title is what remains. Facebook adds
    decoration lines ("Publié il y a 3 jours", "Location") that are dropped as noise.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Matched against the *folded* line: the real text is "Publié il y a 3 jours", and
    # `re.IGNORECASE` does nothing about the accent. Folding is what makes one pattern
    # cover `Publié`, `Publie` and `PUBLIÉ`.
    noise = re.compile(r"^(publie|posted|location|listed|sponsorise|sponsored|nouveau|new)\b")
    lines = [line for line in lines if not noise.match(fold(line))]
    if not lines:
        return {"price_text": None, "title": None, "location_text": None}

    price_text = next((line for line in lines if "$" in line or "CA$" in line), None)
    rest = [line for line in lines if line != price_text]

    location_text = None
    if len(rest) > 1:
        # A trailing "City, QC" line: short, and usually comma-separated.
        candidate = rest[-1]
        if len(candidate) <= 48:
            location_text = candidate
            rest = rest[:-1]

    title = max(rest, key=len) if rest else None
    return {"price_text": price_text, "title": title, "location_text": location_text}


def card_to_listing(card: dict[str, Any]) -> ScrapedListing | None:
    """One extracted anchor -> a :class:`ScrapedListing` with no description yet."""
    item_id = card.get("id")
    if not item_id:
        return None
    parsed = parse_card_text(card.get("text", ""))
    return ScrapedListing(
        platform="facebook",
        external_id=str(item_id),
        url=card.get("href"),
        title=parsed["title"],
        description=None,
        price_text=parsed["price_text"],
        location_text=parsed["location_text"],
        raw={"card_text": card.get("text", "")},
    )


def _import_playwright():
    """Import Playwright lazily, with an install hint instead of an ImportError.

    Guarded the way data-lake guards pyarrow/duckdb: importing this module must not
    require the heavy optional dependency, so the Kijiji-only path works on a plain
    `uv sync` and the whole test suite imports without a browser installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on install state
        raise RuntimeError(PLAYWRIGHT_HINT) from exc
    return sync_playwright


def facebook_login(config: Settings) -> None:  # pragma: no cover - interactive by nature
    """Open a headed browser so you can sign in once. Cookies persist in the profile dir."""
    sync_playwright = _import_playwright()
    with sync_playwright() as driver:
        context = driver.chromium.launch_persistent_context(
            config.facebook_profile_dir,
            headless=False,
            user_agent=config.user_agent,
            locale="fr-CA",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        page.wait_for_url(
            lambda url: "login" not in url and "checkpoint" not in url,
            timeout=300_000,
        )
        context.close()


class FacebookScraper(ListingScraper):
    name = "facebook"

    def __init__(self, config: Settings | None = None, session_factory=None, browser=None) -> None:
        super().__init__(config=config, session_factory=session_factory)
        #: Injected in tests. A `browser` object needs only `.goto(url)`,
        #: `.cards()` and `.detail(url)` — see tests/test_facebook_scraper.py.
        self._browser = browser

    def is_worth_opening(self, listing: ScrapedListing) -> bool:
        """Cheap pre-filter from card data alone: is a detail page load justified?

        Only *contradictions* disqualify. A card with no price or no city is still
        opened, because the detail page is exactly where that information lives — the
        point is to skip the listings already known to be wrong, not to guess.
        """
        price = parse_price(listing.price_text, listing.title)
        if price is not None and (price.monthly > self.config.max_rent_cad or price.is_short_term):
            return False
        if listing.location_text:
            place = locate(
                listing.location_text,
                centre_lat=self.config.centre_lat,
                centre_lon=self.config.centre_lon,
            )
            if place.province is not None and place.province != "QC":
                return False
            if place.distance_km is not None and place.distance_km > self.config.radius_km:
                return False
        return True

    def collect(self, **kwargs) -> tuple[list[ScrapedListing], str | None]:
        browser = self._browser or _PlaywrightBrowser(self.config)
        collected: dict[str, ScrapedListing] = {}
        stopped: str | None = None
        try:
            stopped = self._collect_cards(browser, collected)
            if stopped is None:
                stopped = self._enrich(browser, collected)
        finally:
            if self._browser is None:
                browser.close()
        return list(collected.values()), stopped

    def _collect_cards(self, browser, collected: dict[str, ScrapedListing]) -> str | None:
        """Phase 1 — walk the results grid, scrolling until it stops growing."""
        for url in self.config.facebook_search_urls:
            try:
                self.throttle.wait()
            except (ThrottleExhaustedError, BlockedError) as exc:
                return str(exc)
            browser.goto(url)

            previous = -1
            while len(collected) > previous and len(collected) < self.config.facebook_max_listings:
                previous = len(collected)
                for card in browser.cards():
                    listing = card_to_listing(card)
                    if listing is not None:
                        collected[listing.external_id] = listing
                try:
                    self.throttle.wait()
                except (ThrottleExhaustedError, BlockedError) as exc:
                    return str(exc)
                browser.scroll()
        return None

    def _enrich(self, browser, collected: dict[str, ScrapedListing]) -> str | None:
        """Phase 2 — open only the cards that survived the pre-filter."""
        for external_id, listing in list(collected.items()):
            if not self.is_worth_opening(listing):
                continue
            try:
                self.throttle.wait()
            except (ThrottleExhaustedError, BlockedError) as exc:
                return str(exc)
            detail = browser.detail(listing.url)
            if not detail:
                continue
            collected[external_id] = replace(
                listing,
                description=detail,
                raw={**listing.raw, "detail_text": detail[:8000]},
            )
        return None


class _PlaywrightBrowser:  # pragma: no cover - exercised only against a real browser
    """Thin Playwright wrapper matching the tiny interface the scraper needs."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        sync_playwright = _import_playwright()
        self._driver = sync_playwright().start()
        self._context = self._driver.chromium.launch_persistent_context(
            config.facebook_profile_dir,
            headless=config.facebook_headless,
            user_agent=config.user_agent,
            locale="fr-CA",
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def cards(self) -> list[dict[str, Any]]:
        return list(self._page.evaluate(_CARD_SCRIPT))

    def scroll(self) -> None:
        # A partial-viewport scroll, not `End`: jumping to the bottom of an infinite
        # feed is a behaviour no mouse or trackpad produces.
        self._page.mouse.wheel(0, 1200)

    def detail(self, url: str | None) -> str:
        if not url:
            return ""
        self._page.goto(url, wait_until="domcontentloaded")
        return str(self._page.evaluate(_DETAIL_SCRIPT) or "")

    def dump(self, path: str) -> None:
        """Write the raw card extraction to a file — the `inspect` command's artifact."""
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.cards(), handle, ensure_ascii=False, indent=2)

    def close(self) -> None:
        self._context.close()
        self._driver.stop()
