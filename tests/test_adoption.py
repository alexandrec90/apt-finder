"""This project's adoption list.

The filter *mechanism* is `data_lake.db.adoption.include_only` and is tested upstream.
What is local — and what these tests pin — is the choice of which shared tables
apt-finder materialises, because getting that wrong is silent in both directions: too
broad and the database grows ten empty market-data tables, too narrow and autogenerate
proposes *dropping* the one table this project depends on.
"""

from data_lake.db.base import Base
from data_lake.db.models import Listing

from apt_finder.db.adoption import ADOPTED_LAKE_TABLES, include_object


class FakeChild:
    """A column/index-like object, which Alembic identifies by its parent table."""

    def __init__(self, table):
        self.table = table


def test_listings_is_adopted():
    assert include_object(None, "listings", "table", False, None) is True


def test_the_lakes_market_tables_are_not():
    # These belong to ibkr_trader. They are visible on the shared metadata and must stay
    # out of this project's migrations.
    for name in ("instruments", "price_bars", "news_articles", "social_posts", "features"):
        assert include_object(None, name, "table", False, None) is False, name


def test_every_unadopted_lake_table_is_filtered():
    # Pinned against the real metadata, so a new table added to the lake upstream cannot
    # quietly start appearing in this project's autogenerate output.
    for name in Base.metadata.tables:
        expected = name in ADOPTED_LAKE_TABLES
        assert include_object(None, name, "table", False, None) is expected, name


def test_children_inherit_their_tables_decision():
    listings = Base.metadata.tables["listings"]
    bars = Base.metadata.tables["price_bars"]
    assert include_object(FakeChild(listings), "price_cad", "column", False, None) is True
    assert include_object(FakeChild(bars), "close", "column", False, None) is False


def test_parentless_objects_are_kept():
    # Alembic asks about schema-level objects with no `.table`; excluding those would
    # filter out things the adoption list says nothing about.
    assert include_object(object(), "some_schema", "schema", False, None) is True


def test_the_adopted_table_actually_exists_in_the_lake():
    # Guards the rename case: if `listings` moved or was renamed upstream, the filter
    # would silently exclude everything and autogenerate would propose dropping it.
    assert Listing.__tablename__ in Base.metadata.tables
    assert set(Base.metadata.tables) >= ADOPTED_LAKE_TABLES
