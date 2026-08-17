"""Shared fixtures.

Two rules this suite holds to, both borrowed from data-lake's own test tree:

- **No network, ever.** Scrapers take an injected client/browser; nothing under
  `tests/` may open a socket. A test that needs a page supplies the HTML.
- **No real database.** The session factory is in-memory SQLite, created per test. The
  models are declared with SQLite-friendly variants (`SqliteFriendlyBigInt`,
  `JsonVariant`) precisely so this works without a container running.

`Settings` is always built with `_env_file=None`. Without it, a developer's real `.env`
leaks into the run and the suite passes or fails depending on whose machine it is on.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import data_lake.db.models  # noqa: F401 - registers the lake's tables on Base.metadata
import pytest
from data_lake.db.base import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apt_finder.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Default criteria, isolated from the developer's `.env`."""
    # `_env_file` is a documented pydantic-settings init override, but it is handled at
    # runtime rather than declared on the generated __init__ signature, so mypy cannot
    # see it. (Do not start this note with the words mypy reads as a directive — an
    # explanatory comment in that shape becomes a second, malformed ignore.)
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def session_factory():
    """A zero-arg callable yielding a transactional in-memory SQLite session.

    `StaticPool` plus a shared connection is what keeps the in-memory database alive
    across the several sessions one test opens — a fresh connection would get a fresh,
    empty database.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _open() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _open
