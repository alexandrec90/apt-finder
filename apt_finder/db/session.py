"""Engine/session factory. One engine per process; sessions are short-lived.

This is the object the data-lake package borrows: `apt_finder.runtime.configure_lake()`
hands :func:`get_session` to ``data_lake.configure``, which is the only way that package
ever reaches a database. It owns no engine of its own — by design.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from apt_finder.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """A transactional session: commits on clean exit, rolls back on any exception."""
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
