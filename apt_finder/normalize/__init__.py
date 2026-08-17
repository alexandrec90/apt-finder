"""Turning listing prose into facts.

Every module here is **pure**: text in, structured value out, no network and no
database. That is deliberate — this is the part of the system most likely to be wrong,
and it is the part that costs nothing to test exhaustively. The scrapers are thin
wrappers that feed these functions.
"""
