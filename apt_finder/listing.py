"""The scraper → criteria interface.

One dataclass, deliberately dumb and stringly-typed: a scraper's only job is to hand
over what the page said, unparsed. All interpretation happens in
:mod:`apt_finder.normalize` and :mod:`apt_finder.criteria`, which is what lets the
whole decision layer be tested without a browser or a network.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ScrapedListing:
    """Exactly what one source said about one listing."""

    platform: str
    external_id: str
    url: str | None = None
    title: str | None = None
    description: str | None = None
    #: The price as displayed ("1 450 $ / mois"). Not pre-parsed — see
    #: :mod:`apt_finder.normalize.price` for why the raw string is worth keeping.
    price_text: str | None = None
    #: The source's *location field only*, never the description. Feeding prose to
    #: :func:`apt_finder.normalize.geo.locate` turns every "10 min from Ottawa" into a
    #: false Ontario hit.
    location_text: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    #: Structured attributes the source exposes (Kijiji's attribute list, Facebook's
    #: detail rows). Flattened into the text corpus for appliance detection.
    attributes: dict[str, Any] = field(default_factory=dict)
    posted_at: datetime | None = None
    #: Plaintext seller name, used only to compute a hash. Never persisted — see the
    #: privacy note on :class:`data_lake.db.models.Listing`.
    seller_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def attribute_text(self) -> str:
        """Attributes flattened to one searchable string."""
        return " ".join(f"{key} {value}" for key, value in sorted(self.attributes.items()))

    def text_corpus(self) -> tuple[str | None, ...]:
        """Everything an amenity or dwelling parser should read."""
        return (self.title, self.description, self.attribute_text())
