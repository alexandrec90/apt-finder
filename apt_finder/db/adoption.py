"""Which parts of the shared lake schema this project materialises.

Importing :mod:`data_lake.db.models` registers the lake's *entire* schema on the shared
``Base.metadata`` — today that is mostly ibkr_trader's market data (instruments, price
bars, dividends, fundamentals, news, social posts) plus ``listings``. That is how a shared
lake works: whatever it declares, every consumer sees.

Seeing is not owning. apt-finder keeps exactly one of those tables, so this module names
it and hands the list to :func:`data_lake.db.adoption.include_only`, which builds the
Alembic hook. The mechanism lives upstream because every consumer needs it; only the
*list* is a local decision.

It lives here rather than inside `migrations/env.py` because that file runs migrations as
an import side effect and so cannot be imported by a test.
"""

from data_lake.db.adoption import include_only

__all__ = ["ADOPTED_LAKE_TABLES", "include_object"]

#: Lake tables this project materialises. Add a name when you start using that dataset —
#: a deliberate act, which is the point. Anything unlisted is invisible to autogenerate in
#: *both* directions: never created from the metadata, and never proposed for deletion
#: because it is absent from the database.
ADOPTED_LAKE_TABLES = frozenset({"listings"})

#: The Alembic ``include_object`` hook, wired in `migrations/env.py`.
include_object = include_only(ADOPTED_LAKE_TABLES)
