"""Composition root: hand the data-lake package this project's database, once.

The lake owns no engine and no config system, so a consumer supplies them at its entry
point. We supply exactly one of the two:

- **session factory** — yes. Everything downstream (``Connector.session()``) resolves
  through it.
- **settings** — deliberately not. ``data_lake.settings.LakeSettings`` is a market-data
  credential Protocol; nothing in this project reads it. Leaving it unset means a
  reach for ``Connector.settings`` raises a ``RuntimeError`` naming the fix, which is
  the correct outcome — it can only be a mistake here. See :mod:`apt_finder.config`.

Call :func:`configure_lake` from every entry point (CLI, scheduler, tests that touch a
real database). It is idempotent.
"""

import data_lake

from apt_finder.db.session import get_session


def configure_lake() -> None:
    """Point the data-lake package at this project's session factory."""
    data_lake.configure(session_factory=get_session)
