"""Is this listing in Québec, and is it near Gatineau?

This is the filter most likely to be quietly wrong, because Gatineau and Ottawa are one
bridge apart and every marketplace treats them as one metro area. Searching "Gatineau"
on either site returns Ottawa listings; searching a radius returns mostly Ontario. So
province is decided first and independently, and a listing that cannot be placed is
**rejected, not assumed local** — with Ontario that close, "unknown" is the likelier
error.

Signals, strongest first. The first one that fires decides:

1. **Postal code.** Canada's forward sortation area encodes the province in its first
   letter: ``K``/``L``/``M``/``N``/``P`` are Ontario, ``G``/``H``/``J`` are Québec.
   This is the only signal that cannot be defeated by a seller typing the wrong city,
   and Ontario's ``K`` covers exactly the Ottawa side of the river.
2. **An explicit province token** — ``, QC``, ``Québec``, ``, ON``, ``Ontario``.
3. **A known municipality.** Both lists are needed: recognising Québec towns is not
   enough, because "Gatineau" appears in the *text* of Ottawa listings ("15 min from
   Gatineau"). An Ontario municipality anywhere in the location field outranks a
   Québec one appearing only in prose.

Proximity is then a separate question, answered by coordinates when the source gives
them and by the municipality list when it does not.
"""

import math
from dataclasses import dataclass

from apt_finder.normalize.text import fold

__all__ = ["ONTARIO_NEAR", "QUEBEC_OUTAOUAIS", "Place", "haversine_km", "locate"]

#: First letter of the forward sortation area -> province. Only the provinces that
#: matter here; anything else resolves to a non-QC "other" and is rejected all the same.
_POSTAL_PROVINCE: dict[str, str] = {
    "G": "QC",
    "H": "QC",
    "J": "QC",
    "K": "ON",
    "L": "ON",
    "M": "ON",
    "N": "ON",
    "P": "ON",
}

#: Outaouais (QC) municipalities and Gatineau's five sectors. `gatineau`, `hull`,
#: `aylmer`, `buckingham` and `masson-angers` are the sectors of the merged city; the
#: rest is the surrounding MRC territory a commuter would plausibly accept.
QUEBEC_OUTAOUAIS: tuple[str, ...] = (
    "gatineau",
    "hull",
    "aylmer",
    "buckingham",
    "masson-angers",
    "masson angers",
    "chelsea",
    "cantley",
    "val-des-monts",
    "val des monts",
    "la peche",
    "wakefield",
    "pontiac",
    "luskville",
    "quyon",
    "thurso",
    "l'ange-gardien",
    "ange-gardien",
    "notre-dame-de-la-salette",
    "denholm",
    "low",
    "lac-sainte-marie",
    "gracefield",
    "bowman",
    "mayo",
    "plaisance",
    "papineauville",
    "montebello",
    "fassett",
    "saint-andre-avellin",
    "cheneville",
    "namur",
    "duhamel",
    "ripon",
    "val-des-bois",
    "mulgrave-et-derry",
    "lochaber",
    "maniwaki",
    "outaouais",
)

#: Ontario municipalities and Ottawa neighbourhoods within commuting distance. This
#: list is the actual point of the module: without it, "Ottawa" listings sail through
#: a radius check because they are 4 km from the centre of Gatineau.
ONTARIO_NEAR: tuple[str, ...] = (
    "ottawa",
    "nepean",
    "kanata",
    "orleans",
    "barrhaven",
    "gloucester",
    "vanier",
    "stittsville",
    "rockland",
    "clarence-rockland",
    "embrun",
    "casselman",
    "russell",
    "limoges",
    "arnprior",
    "carleton place",
    "almonte",
    "renfrew",
    "pembroke",
    "cornwall",
    "hawkesbury",
    "manotick",
    "greely",
    "osgoode",
    "metcalfe",
    "richmond",
    "munster",
    "constance bay",
    "dunrobin",
    "carp",
    "navan",
    "cumberland",
    "sarsfield",
    "bourget",
    "alfred",
    "plantagenet",
    "wendover",
    "westboro",
    "hintonburg",
    "centretown",
    "sandy hill",
    "byward",
    "alta vista",
    "riverside south",
    "findlay creek",
    "blackburn hamlet",
    "gatineau hills",  # an Ottawa-side marketing phrase, not a place in Québec
)

_QC_TOKENS = (", qc", " qc ", "(qc)", "quebec", ", qu ", "province de quebec")
_ON_TOKENS = (", on", " on ", "(on)", "ontario", ", ont")

_EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Place:
    """Where a listing is, and how confident we are."""

    province: str | None  # "QC" | "ON" | "other" | None when undecidable
    city: str | None
    postal_prefix: str | None
    latitude: float | None
    longitude: float | None
    distance_km: float | None
    #: Which signal decided the province — recorded so a wrong verdict is debuggable
    #: from the stored row instead of by re-running the scraper.
    evidence: str

    @property
    def is_quebec(self) -> bool:
        return self.province == "QC"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def extract_postal_prefix(folded: str) -> str | None:
    """The forward sortation area (``J8Y``) if the text carries a Canadian postal code.

    Requires the full ``A1A 1A1`` shape, not just the first three characters: ``J8Y``
    alone is indistinguishable from a bus route or a model number, and a false positive
    here overrides every other signal.
    """
    for index in range(len(folded) - 5):
        window = folded[index : index + 7]
        compact = window.replace(" ", "").replace("-", "")[:6]
        if len(compact) < 6:
            continue
        pattern = [str.isalpha, str.isdigit, str.isalpha, str.isdigit, str.isalpha, str.isdigit]
        if all(test(char) for test, char in zip(pattern, compact, strict=True)):
            return compact[:3].upper()
    return None


def _city_from_lists(folded: str) -> tuple[str | None, str | None]:
    """(matched_city, province) from the municipality lists. Ontario wins ties.

    Ontario is checked first on purpose. A Gatineau listing rarely names an Ottawa
    suburb, but Ottawa listings constantly name Gatineau ("minutes to Gatineau",
    "vue sur Gatineau"), so a Québec hit is the weaker evidence of the two.
    """
    for city in ONTARIO_NEAR:
        if city in folded:
            return city, "ON"
    for city in QUEBEC_OUTAOUAIS:
        if city in folded:
            return city, "QC"
    return None, None


def locate(
    location_text: str | None,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    centre_lat: float,
    centre_lon: float,
) -> Place:
    """Resolve a listing's location string (plus optional coordinates) to a :class:`Place`.

    ``location_text`` should be the source's location field — not the whole description.
    Passing the description in makes every "5 minutes from Ottawa" a false Ontario hit.
    """
    folded = fold(location_text)
    postal = extract_postal_prefix(folded)
    city, city_province = _city_from_lists(folded)

    distance = None
    if latitude is not None and longitude is not None:
        distance = haversine_km(latitude, longitude, centre_lat, centre_lon)

    province: str | None = None
    evidence = "none"
    if postal:
        province = _POSTAL_PROVINCE.get(postal[0], "other")
        evidence = f"postal:{postal}"
    elif any(token in f" {folded} " for token in _ON_TOKENS):
        province, evidence = "ON", "token:on"
    elif any(token in f" {folded} " for token in _QC_TOKENS):
        province, evidence = "QC", "token:qc"
    elif city_province:
        province, evidence = city_province, f"city:{city}"

    return Place(
        province=province,
        city=city,
        postal_prefix=postal,
        latitude=latitude,
        longitude=longitude,
        distance_km=distance,
        evidence=evidence,
    )
