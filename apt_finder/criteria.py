"""The search criteria, as one auditable function.

Three verdicts, not two. ``maybe`` exists because a strict yes/no filter throws away
most of the real inventory: short ads simply do not mention a dryer, and roughly half
of Marketplace posts carry no usable location string at all. Rejecting those outright
would produce a very clean, very empty result list.

So the rule is: **contradiction rejects, silence defers.** An ad that says "buanderie
commune" is rejected — it answered the question, and the answer was no. An ad that says
nothing about laundry becomes ``maybe`` and is worth 20 seconds of your own eyes.

The one place this does *not* apply is province. An unplaceable listing is rejected, not
deferred, because Ottawa is across the river and "unknown" there is much more likely to
mean Ontario than a missed Gatineau unit. `Settings.allow_unknown_province` flips it if
you disagree, and the reason code makes the effect visible either way.

Every verdict carries machine-readable reason codes. They are stored on the row, so
tightening a rule later can be replayed over the existing table and diffed instead of
argued about.
"""

from dataclasses import dataclass, field

from apt_finder.config import Settings
from apt_finder.listing import ScrapedListing
from apt_finder.normalize.amenities import ABSENT, UNKNOWN, Appliances, detect_appliances
from apt_finder.normalize.dwelling import Dwelling, parse_dwelling
from apt_finder.normalize.geo import QUEBEC_OUTAOUAIS, Place, locate
from apt_finder.normalize.price import Price, parse_price

__all__ = ["MATCH", "MAYBE", "REJECT", "Evaluation", "evaluate"]

MATCH = "match"
MAYBE = "maybe"
REJECT = "reject"


@dataclass(frozen=True)
class Evaluation:
    """The verdict, the evidence behind it, and everything parsed on the way."""

    verdict: str
    #: Codes that forced a rejection, e.g. ``over_budget``, ``province_not_qc``.
    rejections: list[str] = field(default_factory=list)
    #: Codes for facts the ad never stated, e.g. ``dryer_unknown``.
    unknowns: list[str] = field(default_factory=list)
    price: Price | None = None
    place: Place | None = None
    appliances: Appliances | None = None
    dwelling: Dwelling | None = None

    @property
    def reasons(self) -> list[str]:
        """All codes, for the ``verdict_reasons`` column."""
        return [*self.rejections, *self.unknowns]


def _check_price(
    listing: ScrapedListing, settings: Settings
) -> tuple[Price | None, list[str], list[str]]:
    price = parse_price(listing.price_text, listing.title, listing.description)
    rejections: list[str] = []
    unknowns: list[str] = []
    if price is None:
        unknowns.append("price_unknown")
        return None, rejections, unknowns
    if price.is_short_term:
        # Normalising $650/week to $2,817/month would already push it over budget, but
        # say so explicitly: a weekly quote is a different product, not a dear one.
        rejections.append(f"short_term_{price.period}ly")
    if price.monthly > settings.max_rent_cad:
        rejections.append("over_budget")
    elif price.monthly < settings.min_rent_cad:
        rejections.append("price_implausible")
    return price, rejections, unknowns


def _check_location(
    listing: ScrapedListing, settings: Settings
) -> tuple[Place, list[str], list[str]]:
    place = locate(
        listing.location_text,
        latitude=listing.latitude,
        longitude=listing.longitude,
        centre_lat=settings.centre_lat,
        centre_lon=settings.centre_lon,
    )
    rejections: list[str] = []
    unknowns: list[str] = []

    if place.province is None:
        if settings.allow_unknown_province:
            unknowns.append("province_unknown")
        else:
            rejections.append("province_unknown")
    elif place.province != "QC":
        rejections.append(f"province_not_qc:{place.province.lower()}")

    if place.distance_km is not None:
        if place.distance_km > settings.radius_km:
            rejections.append("outside_radius")
    elif place.city not in QUEBEC_OUTAOUAIS:
        # Province QC is not enough on its own — Québec is large, and a Montréal
        # listing satisfies it perfectly.
        unknowns.append("proximity_unverified")
    return place, rejections, unknowns


def _check_appliances(appliances: Appliances, settings: Settings) -> tuple[list[str], list[str]]:
    rejections: list[str] = []
    unknowns: list[str] = []

    required = []
    if settings.require_washer:
        required.append(("washer", appliances.washer))
    if settings.require_dryer:
        required.append(("dryer", appliances.dryer))
    if settings.require_kitchen_appliances:
        required.extend([("fridge", appliances.fridge), ("stove", appliances.stove)])

    for name, amenity in required:
        if amenity.status == ABSENT:
            rejections.append(f"{name}_absent:{amenity.evidence}")
        elif amenity.status == UNKNOWN:
            unknowns.append(f"{name}_unknown")
    return rejections, unknowns


def _check_dwelling(dwelling: Dwelling, settings: Settings) -> tuple[list[str], list[str]]:
    if not settings.exclude_rooms:
        return [], []
    if dwelling.is_room is True:
        return [f"room_rental:{dwelling.evidence}"], []
    if dwelling.is_room is None:
        return [], ["dwelling_type_unknown"]
    return [], []


def evaluate(listing: ScrapedListing, settings: Settings) -> Evaluation:
    """Apply every criterion to one listing.

    Pure: no database, no network, no clock. That is what makes the whole rule set
    testable from a fixture string, which is the only practical way to keep rules this
    fiddly honest.
    """
    corpus = listing.text_corpus()
    appliances = detect_appliances(*corpus)
    dwelling = parse_dwelling(*corpus)

    price, price_rejects, price_unknowns = _check_price(listing, settings)
    place, place_rejects, place_unknowns = _check_location(listing, settings)
    appliance_rejects, appliance_unknowns = _check_appliances(appliances, settings)
    dwelling_rejects, dwelling_unknowns = _check_dwelling(dwelling, settings)

    rejections = [*price_rejects, *place_rejects, *appliance_rejects, *dwelling_rejects]
    unknowns = [*price_unknowns, *place_unknowns, *appliance_unknowns, *dwelling_unknowns]

    if rejections:
        verdict = REJECT
    elif unknowns:
        verdict = MAYBE
    else:
        verdict = MATCH

    return Evaluation(
        verdict=verdict,
        rejections=rejections,
        unknowns=unknowns,
        price=price,
        place=place,
        appliances=appliances,
        dwelling=dwelling,
    )
