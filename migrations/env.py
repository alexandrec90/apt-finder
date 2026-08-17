"""Alembic environment.

The URL comes from ``DATABASE_URL`` at runtime, never from `alembic.ini` — each
worktree publishes its database on its own host port, so a committed URL would be wrong
for every checkout but one.

``target_metadata`` is the **data-lake** ``Base.metadata``, and importing
:mod:`data_lake.db.models` is what registers tables on it — including ``listings``,
which lives in the lake because it is ordinary shared content, not account data. The
lake owns no migrations, so *this* repo is what actually creates the tables it uses.

**Why the adoption filter.** That one import registers the lake's entire schema, which
today is mostly ibkr_trader's market data: instruments, price bars, dividends,
fundamentals, news, social posts. A consumer is not obliged to materialise all of it —
apt-finder uses exactly one table — but autogenerate compares the *whole* metadata
object against the database, so without a filter it would propose creating ten empty
finance tables here, and `alembic check` would never come back clean.

:mod:`apt_finder.db.adoption` holds that filter and the adoption list. It lives there,
not here, because this file runs migrations as an import side effect and so cannot be
covered by a test.
"""

from logging.config import fileConfig

import data_lake.db.models  # noqa: F401 - registers the lake's tables on Base.metadata
from alembic import context
from data_lake.db.base import Base
from sqlalchemy import engine_from_config, pool

from apt_finder.config import get_settings
from apt_finder.db.adoption import include_object

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
