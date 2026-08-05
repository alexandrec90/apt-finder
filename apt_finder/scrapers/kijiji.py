"""Kijiji — HTTP + the page's own embedded JSON.

Kijiji is a Next.js app, so every search page ships its result set as JSON inside a
``<script id="__NEXT_DATA__">`` tag. Reading that is dramatically more robust than
scraping the rendered DOM: the JSON is the data the page was built from, it carries
fields the DOM never shows (coordinates, activation dates, structured attributes), and
it does not change every time someone adjusts a CSS class.

It also means no browser is needed here — plain HTTP, one page at a time, paced by
:class:`~apt_finder.scrapers.throttle.Throttle`.

**The part that needs your eyes once.** Kijiji does not document this JSON, and its key
names drift. Every field is read through :func:`_first`, which tries several aliases and
tolerates all of them being absent, and the node walker recognises listings structurally
rather than by path. That degrades to "found nothing" instead of crashing or, worse,
silently mapping the wrong field. Run ``apt-finder inspect kijiji`` after any long gap:
it dumps what the parser actually saw to ``logs/`` so a drift is a two-minute fix.

The search URLs in ``Settings.kijiji_search_urls`` carry Kijiji's category and location
ids (``c37l1700242``). Verify them against the live site before the first real run — a
stale id returns an unrelated result set rather than an error.
"""

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from apt_finder.config import Settings
from apt_finder.listing import ScrapedListing
from apt_finder.scrapers.base import ListingScraper
from apt_finder.scrapers.throttle import BlockedError, ThrottleExhaustedError

__all__ = ["KijijiScraper", "extract_next_data", "iter_listing_nodes", "node_to_listing"]

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)

#: Kijiji's JSON quotes money in integer cents. A float, or an int small enough to be a
#: plausible monthly rent on its own, is taken at face value instead — the two cases are
#: distinguishable and getting it wrong is a factor-of-100 error either way.
_CENTS_THRESHOLD = 10_000

#: Status codes that mean "slow down" rather than "not found".
_BLOCK_STATUSES = frozenset({403, 429, 503})


def extract_next_data(html: str) -> dict[str, Any] | None:
    """Pull and parse the ``__NEXT_DATA__`` payload out of a page. None if absent."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _first(node: dict[str, Any], *keys: str) -> Any:
    """First present, non-empty value among several key aliases."""
    for key in keys:
        value = node.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _looks_like_listing(node: Any) -> bool:
    """Structural test: an id plus a title, and something price- or location-shaped.

    Deliberately not a path lookup. Kijiji has moved the results array between
    ``props.pageProps.__APOLLO_STATE__``, ``...searchResults.results`` and elsewhere;
    recognising the shape survives all of that.
    """
    if not isinstance(node, dict):
        return False
    has_id = any(key in node for key in ("id", "listingId", "adId"))
    has_title = any(key in node for key in ("title", "name"))
    has_detail = any(
        key in node for key in ("price", "priceLabel", "location", "address", "seoUrl", "url")
    )
    return has_id and has_title and has_detail


def iter_listing_nodes(data: Any) -> Iterator[dict[str, Any]]:
    """Walk arbitrary nested JSON, yielding every listing-shaped dict exactly once."""
    seen: set[int] = set()
    stack: list[Any] = [data]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if _looks_like_listing(current) and id(current) not in seen:
                seen.add(id(current))
                yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _price_text(value: Any) -> str | None:
    """Render whatever Kijiji put in ``price`` as text for the price parser.

    Prefers a preformatted label when the payload has one — that string is what a human
    saw, so it already carries the period ("1 450 $ / mois") the numeric field lacks.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return _amount_text(value)
    if isinstance(value, dict):
        label = _first(value, "label", "formatted", "display", "text", "priceLabel")
        if isinstance(label, str):
            return label
        amount = _first(value, "amount", "value", "cents")
        if isinstance(amount, int | float):
            return _amount_text(amount)
    return None


def _amount_text(amount: float) -> str:
    """Numeric price -> a dollar string, applying the cents convention."""
    if isinstance(amount, int) and abs(amount) >= _CENTS_THRESHOLD:
        amount = amount / 100
    return f"{amount:.2f} $"


def _location_text(node: dict[str, Any]) -> tuple[str | None, float | None, float | None]:
    """The location *field* and its coordinates — never the description.

    Returning them together keeps the caller from accidentally passing prose to
    :func:`apt_finder.normalize.geo.locate`, which is the mistake that lets Ottawa in.
    """
    raw = _first(node, "location", "address", "locationName")
    latitude = longitude = None
    text: str | None = None

    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, dict):
        name = _first(raw, "name", "address", "displayName", "label", "fullName")
        text = name if isinstance(name, str) else None
        coords = raw.get("coordinates") if isinstance(raw.get("coordinates"), dict) else raw
        if isinstance(coords, dict):
            lat = _first(coords, "latitude", "lat")
            lon = _first(coords, "longitude", "lng", "lon")
            latitude = float(lat) if isinstance(lat, int | float) else None
            longitude = float(lon) if isinstance(lon, int | float) else None

    if latitude is None:
        lat = _first(node, "latitude", "lat")
        latitude = float(lat) if isinstance(lat, int | float) else None
    if longitude is None:
        lon = _first(node, "longitude", "lng", "lon")
        longitude = float(lon) if isinstance(lon, int | float) else None

    # Postal code lives beside the address often enough to be worth appending: it is
    # the strongest province signal we have.
    postal = _first(node, "postalCode", "zip") if isinstance(node, dict) else None
    if isinstance(raw, dict) and postal is None:
        postal = _first(raw, "postalCode", "zip")
    if isinstance(postal, str):
        text = f"{text} {postal}" if text else postal
    return text, latitude, longitude


def _attributes(node: dict[str, Any]) -> dict[str, Any]:
    """Kijiji's structured attribute list, flattened to a plain dict.

    These matter more than the description for appliance detection: an ad whose body is
    three words often still ticks "Laveuse/Sécheuse" in the structured fields.
    """
    raw = _first(node, "attributes", "adAttributes", "details")
    flat: dict[str, Any] = {}
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items()}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = _first(item, "name", "key", "label", "canonicalName")
            value = _first(item, "value", "values", "localizedValue", "displayValue")
            if key is not None:
                flat[str(key)] = value
    return flat


def _posted_at(node: dict[str, Any]) -> datetime | None:
    raw = _first(node, "activationDate", "sortingDate", "postedAt", "creationDate", "date")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def node_to_listing(node: dict[str, Any]) -> ScrapedListing | None:
    """Map one Kijiji JSON node to a :class:`ScrapedListing`. None if it lacks an id."""
    external_id = _first(node, "id", "listingId", "adId")
    if external_id is None:
        return None
    title = _first(node, "title", "name")
    url = _first(node, "seoUrl", "url", "href", "link")
    if isinstance(url, str) and url.startswith("/"):
        url = f"https://www.kijiji.ca{url}"

    location_text, latitude, longitude = _location_text(node)
    seller = _first(node, "sellerName", "posterName", "userName")

    return ScrapedListing(
        platform="kijiji",
        external_id=str(external_id),
        url=url if isinstance(url, str) else None,
        title=title if isinstance(title, str) else None,
        description=_first(node, "description", "shortDescription", "body", "snippet"),
        price_text=_price_text(_first(node, "price", "priceLabel", "amount")),
        location_text=location_text,
        latitude=latitude,
        longitude=longitude,
        attributes=_attributes(node),
        posted_at=_posted_at(node),
        seller_name=seller if isinstance(seller, str) else None,
        # The node is kept whole: a parser fix must never require re-scraping, which is
        # the expensive and ban-risking half of this system.
        raw=node,
    )


def parse_search_page(html: str) -> list[ScrapedListing]:
    """Every listing on one search-results page. Empty list if the page has none."""
    data = extract_next_data(html)
    if data is None:
        return []
    listings = [node_to_listing(node) for node in iter_listing_nodes(data)]
    return [listing for listing in listings if listing is not None]


def page_url(base: str, page: int) -> str:
    """Kijiji paginates with a ``/page-N`` path segment before the category suffix."""
    if page <= 1:
        return base
    head, _, tail = base.rstrip("/").rpartition("/")
    return f"{head}/page-{page}/{tail}" if head else base


class KijijiScraper(ListingScraper):
    name = "kijiji"

    def __init__(self, config: Settings | None = None, session_factory=None, client=None) -> None:
        super().__init__(config=config, session_factory=session_factory)
        # Injected in tests; nothing here ever opens a real socket under pytest.
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self.config.user_agent,
            # French first: the site serves FR content for Québec, and asking for it
            # explicitly is both more honest and better for the parsers downstream.
            "Accept-Language": "fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if self.config.kijiji_cookie:
            headers["Cookie"] = self.config.kijiji_cookie
        return headers

    def collect(self, **kwargs) -> tuple[list[ScrapedListing], str | None]:
        client = self._client or httpx.Client(
            timeout=self.config.http_timeout_seconds, follow_redirects=True
        )
        collected: dict[str, ScrapedListing] = {}
        stopped: str | None = None
        try:
            for base in self.config.kijiji_search_urls:
                for page in range(1, self.config.kijiji_max_pages + 1):
                    try:
                        self.throttle.wait()
                    except (ThrottleExhaustedError, BlockedError) as exc:
                        return list(collected.values()), str(exc)

                    response = client.get(page_url(base, page), headers=self._headers())
                    if response.status_code in _BLOCK_STATUSES:
                        try:
                            self.throttle.note_block()
                        except BlockedError as exc:
                            return list(collected.values()), str(exc)
                        continue
                    if response.status_code != 200:
                        stopped = f"http {response.status_code} on {page_url(base, page)}"
                        break
                    self.throttle.note_success()

                    found = parse_search_page(response.text)
                    if not found:
                        # An empty page is the normal end of pagination, not an error.
                        break
                    for listing in found:
                        collected[listing.external_id] = listing
        finally:
            if self._client is None:
                client.close()
        return list(collected.values()), stopped
