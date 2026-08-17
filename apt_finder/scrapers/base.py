"""What every scraper shares: evaluate, upsert, count.

Subclasses implement :meth:`ListingScraper.collect` — "give me what the site said" —
and inherit everything after that. Keeping persistence in one place is what makes the
two very different fetch strategies (an HTTP client for Kijiji, a real browser for
Facebook) produce identical rows.

**On the data-lake seam.** This inherits ``data_lake.ingestion.base.Connector`` for its
session plumbing and its idempotency contract (always upsert, never blind-insert), but
passes ``settings=None`` to it on purpose: that package's settings Protocol describes
market-data credentials, and this project configures only the session factory. Scraper
knobs come from ``self.config``. See :mod:`apt_finder.config`.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from data_lake.db.models import Listing
from data_lake.ingestion.base import Connector, stable_hash
from sqlalchemy import select

from apt_finder.config import Settings, get_settings
from apt_finder.criteria import MATCH, MAYBE, REJECT, Evaluation, evaluate
from apt_finder.listing import ScrapedListing
from apt_finder.scrapers.throttle import Throttle

__all__ = ["ListingScraper", "ScrapeResult"]


@dataclass
class ScrapeResult:
    """What one run did. Returned by every scraper and printed by the CLI."""

    platform: str
    seen: int = 0
    new: int = 0
    updated: int = 0
    by_verdict: dict[str, int] = field(default_factory=lambda: {MATCH: 0, MAYBE: 0, REJECT: 0})
    #: Set when the run stopped early — a tripped breaker or a spent budget. The rows
    #: already collected are still committed; a partial sweep beats a banned account.
    stopped_early: str | None = None

    def summary(self) -> str:
        parts = [
            f"{self.platform}: {self.seen} seen",
            f"{self.new} new",
            f"{self.updated} updated",
            f"{self.by_verdict[MATCH]} match",
            f"{self.by_verdict[MAYBE]} maybe",
            f"{self.by_verdict[REJECT]} reject",
        ]
        if self.stopped_early:
            parts.append(f"STOPPED EARLY ({self.stopped_early})")
        return " | ".join(parts)


class ListingScraper(Connector):
    """One rental source."""

    name = "override-me"

    def __init__(self, config: Settings | None = None, session_factory=None) -> None:
        # settings=None: the lake's settings Protocol is not ours to satisfy.
        super().__init__(settings=None, session_factory=session_factory)
        self.config = config or get_settings()
        self.throttle = Throttle(self.config, name=self.name)

    def collect(self, **kwargs) -> tuple[list[ScrapedListing], str | None]:
        """Fetch raw listings. Returns (listings, stopped_early_reason).

        Implementations must return what they collected even when they stop early —
        a run aborted by the circuit breaker should still persist its first two pages.
        """
        raise NotImplementedError

    def fetch(self, **kwargs) -> int:
        """Collect, evaluate, and upsert. Returns rows written (the ``Connector`` contract)."""
        result = self.run(**kwargs)
        return result.new + result.updated

    def run(self, **kwargs) -> ScrapeResult:
        """Like :meth:`fetch`, but returns the full :class:`ScrapeResult`."""
        listings, stopped = self.collect(**kwargs)
        result = self.persist(listings)
        result.stopped_early = stopped
        return result

    def persist(self, listings: list[ScrapedListing]) -> ScrapeResult:
        """Upsert each listing with its verdict. Idempotent on ``(platform, external_id)``."""
        result = ScrapeResult(platform=self.name)
        now = datetime.now(UTC)
        with self.session() as session:
            for scraped in listings:
                verdict = evaluate(scraped, self.config)
                existing = session.scalar(
                    select(Listing).where(
                        Listing.platform == scraped.platform,
                        Listing.external_id == scraped.external_id,
                    )
                )
                if existing is None:
                    session.add(_to_row(scraped, verdict, now, first_seen=now))
                    result.new += 1
                else:
                    _apply(existing, scraped, verdict, now)
                    result.updated += 1
                result.seen += 1
                result.by_verdict[verdict.verdict] += 1
        return result


def _to_row(
    scraped: ScrapedListing, verdict: Evaluation, now: datetime, *, first_seen: datetime
) -> Listing:
    row = Listing(
        platform=scraped.platform,
        external_id=scraped.external_id,
        first_seen_at=first_seen,
        verdict=verdict.verdict,
        last_seen_at=now,
    )
    _apply(row, scraped, verdict, now)
    return row


def _apply(row: Listing, scraped: ScrapedListing, verdict: Evaluation, now: datetime) -> None:
    """Copy the scrape and its verdict onto a row. Never touches ``first_seen_at``.

    Re-scraping refreshes everything volatile — price changes, edited descriptions, and
    the verdict itself, which is what lets a criteria change be replayed over stored
    rows. ``first_seen_at`` is the one field that must survive, because "how long has
    this been listed?" is the signal that tells a real vacancy from a stale repost.
    """
    row.url = scraped.url
    row.title = scraped.title
    row.description = scraped.description
    row.last_seen_at = now
    row.posted_at = scraped.posted_at
    row.seller_hash = stable_hash(scraped.seller_name) if scraped.seller_name else None
    row.raw = scraped.raw or None

    if verdict.price is not None:
        row.price_cad = verdict.price.monthly
        row.price_period = verdict.price.period
    if verdict.place is not None:
        row.city = verdict.place.city
        row.province = verdict.place.province
        row.postal_prefix = verdict.place.postal_prefix
        row.latitude = verdict.place.latitude
        row.longitude = verdict.place.longitude
        row.distance_km = verdict.place.distance_km
    if verdict.dwelling is not None:
        row.rooms = verdict.dwelling.rooms
        row.bedrooms = verdict.dwelling.bedrooms
        row.unit_type = verdict.dwelling.unit_type
        row.is_room_rental = verdict.dwelling.is_room
    if verdict.appliances is not None:
        row.washer = verdict.appliances.washer.status
        row.dryer = verdict.appliances.dryer.status
        row.fridge = verdict.appliances.fridge.status
        row.stove = verdict.appliances.stove.status
        row.dishwasher = verdict.appliances.dishwasher.status

    row.verdict = verdict.verdict
    row.verdict_reasons = verdict.reasons
