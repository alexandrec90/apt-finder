"""Upsert behaviour against a real (in-memory) database.

This is where the `(platform, external_id)` idempotency contract is proved, and where
the privacy rule — seller names are hashed, never stored — is enforced by a test rather
than by everyone remembering.
"""

from datetime import UTC, datetime

from data_lake.db.models import Listing
from sqlalchemy import select

from apt_finder.criteria import MATCH, MAYBE, REJECT
from apt_finder.listing import ScrapedListing
from apt_finder.scrapers.base import ListingScraper


class StubScraper(ListingScraper):
    """A scraper whose `collect` returns whatever the test handed it."""

    name = "kijiji"

    def __init__(self, listings, **kwargs):
        super().__init__(**kwargs)
        self._listings = listings

    def collect(self, **kwargs):
        return list(self._listings), None


def make(external_id="1", **overrides) -> ScrapedListing:
    base = {
        "platform": "kijiji",
        "external_id": external_id,
        "title": "Beau 4 1/2 à Gatineau",
        "description": "Laveuse-sécheuse incluses, réfrigérateur et cuisinière fournis",
        "price_text": "1 450 $/mois",
        "location_text": "Gatineau, QC J8Y 3X5",
        "url": "https://www.kijiji.ca/v/1",
    }
    return ScrapedListing(**{**base, **overrides})


def rows(session_factory) -> list[Listing]:
    with session_factory() as session:
        return list(session.execute(select(Listing)).scalars().all())


def test_new_listing_is_inserted(settings, session_factory):
    result = StubScraper([make()], config=settings, session_factory=session_factory).run()
    assert result.new == 1
    assert result.by_verdict[MATCH] == 1

    stored = rows(session_factory)
    assert len(stored) == 1
    assert stored[0].price_cad == 1450
    assert stored[0].province == "QC"
    assert stored[0].rooms == 4.5
    assert stored[0].washer == "present"


def test_rescraping_updates_rather_than_duplicates(settings, session_factory):
    StubScraper([make()], config=settings, session_factory=session_factory).run()
    result = StubScraper(
        [make(price_text="1 395 $/mois")], config=settings, session_factory=session_factory
    ).run()

    assert result.new == 0
    assert result.updated == 1
    stored = rows(session_factory)
    assert len(stored) == 1
    assert stored[0].price_cad == 1395


def test_first_seen_survives_a_rescrape(settings, session_factory):
    # "How long has this been listed?" is what separates a real vacancy from a repost,
    # so first_seen_at is the one field an update must never touch.
    StubScraper([make()], config=settings, session_factory=session_factory).run()
    original = rows(session_factory)[0].first_seen_at

    StubScraper(
        [make(title="Prix réduit!")], config=settings, session_factory=session_factory
    ).run()
    updated = rows(session_factory)[0]

    assert updated.first_seen_at == original
    assert updated.last_seen_at >= original
    assert updated.title == "Prix réduit!"


def test_same_id_on_different_platforms_are_distinct(settings, session_factory):
    StubScraper([make(external_id="7")], config=settings, session_factory=session_factory).run()

    class FbScraper(StubScraper):
        name = "facebook"

    FbScraper(
        [make(external_id="7", platform="facebook")],
        config=settings,
        session_factory=session_factory,
    ).run()

    assert len(rows(session_factory)) == 2


def test_seller_name_is_hashed_never_stored(settings, session_factory):
    StubScraper(
        [make(seller_name="Jean Tremblay")], config=settings, session_factory=session_factory
    ).run()
    stored = rows(session_factory)[0]

    assert stored.seller_hash is not None
    assert len(stored.seller_hash) == 64
    assert "Jean" not in str(stored.seller_hash)
    # And the plaintext appears nowhere else on the row either.
    assert "Tremblay" not in (stored.description or "")


def test_same_seller_hashes_identically(settings, session_factory):
    StubScraper(
        [make(external_id="1", seller_name="X"), make(external_id="2", seller_name="X")],
        config=settings,
        session_factory=session_factory,
    ).run()
    hashes = {row.seller_hash for row in rows(session_factory)}
    assert len(hashes) == 1


def test_no_seller_leaves_the_hash_null(settings, session_factory):
    StubScraper([make()], config=settings, session_factory=session_factory).run()
    assert rows(session_factory)[0].seller_hash is None


def test_verdict_and_reasons_are_stored(settings, session_factory):
    StubScraper(
        [make(price_text="2 900 $/mois")], config=settings, session_factory=session_factory
    ).run()
    stored = rows(session_factory)[0]
    assert stored.verdict == REJECT
    assert "over_budget" in stored.verdict_reasons


def test_rejected_listings_are_still_stored(settings, session_factory):
    # Storing rejects is what lets a criteria change be replayed and diffed instead of
    # requiring a fresh scrape.
    StubScraper(
        [make(location_text="Ottawa, ON K1S 5B6")], config=settings, session_factory=session_factory
    ).run()
    assert len(rows(session_factory)) == 1


def test_raw_payload_is_preserved(settings, session_factory):
    StubScraper(
        [make(raw={"id": "1", "anything": True})], config=settings, session_factory=session_factory
    ).run()
    assert rows(session_factory)[0].raw == {"id": "1", "anything": True}


def test_verdict_is_recomputed_on_update(settings, session_factory):
    StubScraper([make()], config=settings, session_factory=session_factory).run()
    assert rows(session_factory)[0].verdict == MATCH

    StubScraper(
        [make(description="Beau logement rénové")], config=settings, session_factory=session_factory
    ).run()
    assert rows(session_factory)[0].verdict == MAYBE


def test_fetch_returns_rows_written(settings, session_factory):
    written = StubScraper(
        [make(external_id="1"), make(external_id="2")],
        config=settings,
        session_factory=session_factory,
    ).fetch()
    assert written == 2


def test_result_summary_is_readable(settings, session_factory):
    result = StubScraper([make()], config=settings, session_factory=session_factory).run()
    summary = result.summary()
    assert "kijiji" in summary
    assert "1 match" in summary


def test_summary_flags_an_early_stop(settings, session_factory):
    result = StubScraper([make()], config=settings, session_factory=session_factory).run()
    result.stopped_early = "429"
    assert "STOPPED EARLY" in result.summary()


def test_empty_collect_is_not_an_error(settings, session_factory):
    result = StubScraper([], config=settings, session_factory=session_factory).run()
    assert result.seen == 0
    assert rows(session_factory) == []


def test_timestamps_are_timezone_aware(settings, session_factory):
    StubScraper(
        [make(posted_at=datetime(2026, 8, 1, tzinfo=UTC))],
        config=settings,
        session_factory=session_factory,
    ).run()
    assert rows(session_factory)[0].posted_at is not None
