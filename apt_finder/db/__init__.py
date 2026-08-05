"""Database layer: the engine, the session factory, and which lake tables we adopt.

No models are declared here. ``listings`` lives in ``data_lake.db.models`` — the lake's
boundary is account-shape, not subject matter, and scraped public listings are ordinary
shared content. This package supplies the engine and the migrations the lake deliberately
does not own. See :mod:`apt_finder.db.adoption` for which of the shared tables this
project materialises.
"""
