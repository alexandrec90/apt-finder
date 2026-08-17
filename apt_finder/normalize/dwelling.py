"""Is this a whole apartment, or a room in someone else's?

The distinction is not as obvious as it sounds, because the word ``chambre`` appears in
both answers:

- ``chambre à louer dans un 5½`` — a room. Not what we want.
- ``5½ avec 2 chambres à coucher`` — a two-bedroom apartment. Exactly what we want.

So room detection is **phrase-based, never word-based**. Matching bare ``chambre``
rejects most of the real inventory in Gatineau, where bedroom counts are routinely
advertised that way.

The other local convention encoded here is Québec room notation: ``3½`` counts *all*
rooms (the ½ is the bathroom), so a 3½ is a one-bedroom, a 4½ is a two-bedroom, and a
1½ is a bachelor. It is how everyone in the province searches, and it appears far more
often than a bedroom count does. We keep the raw room figure rather than converting,
because the mapping to bedrooms is conventional rather than exact — and store a derived
bedroom count alongside it.
"""

import re
from dataclasses import dataclass

from apt_finder.normalize.text import fold

__all__ = ["Dwelling", "parse_dwelling", "rooms_to_bedrooms"]

#: Phrases that mean "a room inside a dwelling someone else also lives in".
ROOM_PHRASES: tuple[str, ...] = (
    "chambre a louer",
    "chambres a louer",
    "chambre a sous-louer",
    "chambre disponible",
    "chambres disponibles",
    "chambre dans",
    "chambre meublee dans",
    "chambre pour",
    "maison de chambres",
    "colocation",
    "colocataire",
    "coloc recherche",
    "recherche coloc",
    "room for rent",
    "rooms for rent",
    "room to rent",
    "room available",
    "room rental",
    "rooming house",
    "roommate",
    "room mate",
    "shared house",
    "shared apartment",
    "shared accommodation",
    "shared living",
    "logement partage",
    "appartement partage",
)

#: Phrases that mean "a self-contained unit".
UNIT_PHRASES: tuple[str, ...] = (
    "appartement",
    "logement",
    "condo",
    "studio",
    "loft",
    "bachelor",
    "duplex",
    "triplex",
    "quadruplex",
    "maison de ville",
    "townhouse",
    "jumele",
    "bungalow",
    "apartment",
    "unit entier",
    "entire unit",
    "whole unit",
)

_UNIT_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("studio", ("studio", "bachelor")),
    ("loft", ("loft",)),
    ("condo", ("condo",)),
    ("house", ("maison", "bungalow", "house", "cottage")),
    ("townhouse", ("maison de ville", "townhouse", "jumele")),
    ("duplex", ("duplex", "triplex", "quadruplex")),
    ("basement", ("sous-sol", "basement", "demi sous-sol")),
    ("apartment", ("appartement", "logement", "apartment", "flat")),
)

#: `4 1/2`, `4½`, `4 ½`, `4-1/2` — all four arrive here as some spacing of `4` + `1/2`,
#: because :func:`~apt_finder.normalize.text.fold` has already expanded the vulgar
#: fraction and normalised its slash. Matching a literal `½` here would be dead code.
#: The `(?<![\d,.])` guard stops `14 1/2` being read as a 4½ and, more importantly,
#: stops a price like `1 450` from ever reaching this pattern.
_ROOMS_RE = re.compile(r"(?<![\d,.])(\d{1,2})\s*[-\s]?\s*1/2")
#: A plain "3 pieces" / "3 pcs" room count, the other way it gets written.
_PIECES_RE = re.compile(r"(?<![\d,.])(\d{1,2})\s*(?:pieces?|pcs?)\b")
#: `2 chambres`, `2 cac`, `2 c.a.c`, `2 bedrooms`, `2 bdrm`, `2 br`.
_BEDROOMS_RE = re.compile(
    r"(?<![\d,.])(\d{1,2})\s*(?:chambres?\s*(?:a\s*coucher)?|cac\b|c\.?a\.?c\b|"
    r"bedrooms?|bdrms?|bdr\b|br\b)"
)

#: Above this, the number is not a room count — it is a street number or an area.
_MAX_ROOMS = 12


@dataclass(frozen=True)
class Dwelling:
    """What kind of place this is."""

    #: True = a room/colocation, False = a self-contained unit, None = undecidable.
    is_room: bool | None
    #: Québec room notation as a number: "4 1/2" -> 4.5.
    rooms: float | None
    bedrooms: float | None
    unit_type: str | None
    evidence: str | None = None


def rooms_to_bedrooms(rooms: float) -> float:
    """Québec room count -> bedrooms, by the usual convention.

    A 3½ is living room + kitchen + one bedroom + bathroom, so bedrooms = rooms - 2.5.
    A 1½ is a bachelor: 0 bedrooms, not a negative number.
    """
    return max(0.0, rooms - 2.5)


def _first_int(pattern: re.Pattern[str], folded: str) -> int | None:
    for match in pattern.finditer(folded):
        value = int(match.group(1))
        if 0 < value <= _MAX_ROOMS:
            return value
    return None


def parse_dwelling(*sources: str | None) -> Dwelling:
    """Classify a listing from its title, description and category text."""
    folded = " ".join(fold(source) for source in sources if source)

    rooms: float | None = None
    if (whole := _first_int(_ROOMS_RE, folded)) is not None:
        rooms = whole + 0.5
    elif (pieces := _first_int(_PIECES_RE, folded)) is not None:
        rooms = float(pieces)

    bedrooms: float | None = None
    if (count := _first_int(_BEDROOMS_RE, folded)) is not None:
        bedrooms = float(count)
    elif rooms is not None:
        bedrooms = rooms_to_bedrooms(rooms)

    unit_type = next(
        (name for name, terms in _UNIT_TYPES if any(term in folded for term in terms)), None
    )

    room_hit = next((phrase for phrase in ROOM_PHRASES if phrase in folded), None)
    unit_hit = next((phrase for phrase in UNIT_PHRASES if phrase in folded), None)

    if room_hit:
        # A room ad frequently *also* says "dans un 5½" — describing the host's unit,
        # not what is for rent. The room phrase is the specific claim, so it wins.
        return Dwelling(True, rooms, bedrooms, unit_type, f"room:{room_hit}")
    if unit_hit:
        return Dwelling(False, rooms, bedrooms, unit_type, f"unit:{unit_hit}")
    if rooms is not None:
        # Bare "4 1/2" with no other noun is still unambiguous locally: nobody
        # advertises a single room that way.
        return Dwelling(False, rooms, bedrooms, unit_type, f"rooms:{rooms}")
    return Dwelling(None, rooms, bedrooms, unit_type, None)
