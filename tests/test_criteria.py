"""The criteria pipeline: contradiction rejects, silence defers."""

from decimal import Decimal

import pytest

from apt_finder.criteria import MATCH, MAYBE, REJECT, evaluate
from apt_finder.listing import ScrapedListing


def make(**overrides) -> ScrapedListing:
    """A listing that matches everything, so each test changes exactly one thing."""
    base = {
        "platform": "kijiji",
        "external_id": "1",
        "title": "Beau 4 1/2 à Gatineau",
        "description": (
            "Logement rénové, laveuse-sécheuse incluses, réfrigérateur et cuisinière fournis."
        ),
        "price_text": "1 450 $/mois",
        "location_text": "Gatineau, QC J8Y 3X5",
    }
    return ScrapedListing(**{**base, **overrides})


def test_the_ideal_listing_matches(settings):
    result = evaluate(make(), settings)
    assert result.verdict == MATCH
    assert result.reasons == []
    assert result.price.monthly == Decimal("1450.00")
    assert result.place.province == "QC"


# --- budget ---------------------------------------------------------------


def test_over_budget_is_rejected(settings):
    result = evaluate(make(price_text="2 400 $/mois"), settings)
    assert result.verdict == REJECT
    assert "over_budget" in result.rejections


def test_exactly_at_budget_matches(settings):
    assert evaluate(make(price_text="2 000 $/mois"), settings).verdict == MATCH


def test_implausibly_cheap_is_rejected(settings):
    # A $1 listing is a placeholder or a scam, not a find.
    result = evaluate(make(price_text="1 $/mois"), settings)
    assert "price_implausible" in result.rejections


def test_weekly_rental_is_rejected_as_short_term(settings):
    result = evaluate(make(price_text="600 $/semaine"), settings)
    assert result.verdict == REJECT
    assert any(reason.startswith("short_term") for reason in result.rejections)


def test_missing_price_is_a_maybe(settings):
    result = evaluate(
        make(price_text=None, description="Laveuse-sécheuse, frigo, cuisinière"), settings
    )
    assert result.verdict == MAYBE
    assert "price_unknown" in result.unknowns


# --- geography ------------------------------------------------------------


def test_ontario_is_rejected(settings):
    result = evaluate(make(location_text="Ottawa, ON K1S 5B6"), settings)
    assert result.verdict == REJECT
    assert "province_not_qc:on" in result.rejections


def test_unknown_province_is_rejected_by_default(settings):
    # Fail closed: Ottawa is across the river, so "unknown" is the risky answer.
    result = evaluate(make(location_text="Disponible maintenant"), settings)
    assert result.verdict == REJECT
    assert "province_unknown" in result.rejections


def test_unknown_province_can_be_downgraded_to_maybe(settings):
    settings.allow_unknown_province = True
    result = evaluate(make(location_text=""), settings)
    assert result.verdict == MAYBE
    assert "province_unknown" in result.unknowns


def test_outside_the_radius_is_rejected(settings):
    # Québec City: right province, wrong end of it.
    result = evaluate(make(latitude=46.8139, longitude=-71.2080), settings)
    assert "outside_radius" in result.rejections


def test_quebec_far_from_gatineau_without_coordinates_is_a_maybe(settings):
    result = evaluate(make(location_text="Trois-Rivières, QC"), settings)
    assert result.verdict == MAYBE
    assert "proximity_unverified" in result.unknowns


def test_known_outaouais_city_needs_no_coordinates(settings):
    assert evaluate(make(location_text="Aylmer, QC"), settings).verdict == MATCH


# --- appliances -----------------------------------------------------------


def test_hookup_only_is_rejected(settings):
    result = evaluate(
        make(description="Espace pour laveuse-sécheuse. Frigo et cuisinière inclus."), settings
    )
    assert result.verdict == REJECT
    assert any(reason.startswith("washer_absent") for reason in result.rejections)


def test_shared_laundry_is_rejected(settings):
    # The laundry words must actually appear: "buanderie commune" alone leaves the
    # washer *unmentioned*, which is a `maybe`, not a rejection. (This test previously
    # used that fixture and passed only because `commune` was leaking across a sentence
    # boundary and negating the fridge instead.)
    result = evaluate(
        make(description="Laveuse et sécheuse communes au sous-sol. Frigo et cuisinière inclus."),
        settings,
    )
    assert result.verdict == REJECT
    assert any(reason.startswith("washer_absent") for reason in result.rejections)


def test_unnamed_shared_laundry_is_only_a_maybe(settings):
    # "Buanderie commune" never says whether the *unit* has a washer, so it defers
    # rather than rejecting — silence defers, contradiction rejects.
    result = evaluate(
        make(description="Buanderie commune au sous-sol. Frigo et cuisinière inclus."), settings
    )
    assert result.verdict == MAYBE
    assert "washer_unknown" in result.unknowns


def test_unmentioned_laundry_is_a_maybe(settings):
    result = evaluate(
        make(description="Beau logement rénové, frigo et cuisinière inclus"), settings
    )
    assert result.verdict == MAYBE
    assert "washer_unknown" in result.unknowns
    assert "dryer_unknown" in result.unknowns


def test_kitchen_appliances_can_be_waived(settings):
    settings.require_kitchen_appliances = False
    result = evaluate(make(description="Laveuse et sécheuse incluses"), settings)
    assert result.verdict == MATCH


def test_dryer_requirement_can_be_waived(settings):
    settings.require_dryer = False
    result = evaluate(
        make(description="Laveuse incluse, frigo et cuisinière fournis, pas de sécheuse"), settings
    )
    assert result.verdict == MATCH


# --- dwelling type --------------------------------------------------------


def test_room_rental_is_rejected(settings):
    result = evaluate(
        make(
            title="Chambre à louer dans un 5 1/2",
            description="Laveuse-sécheuse, frigo et cuisinière inclus",
        ),
        settings,
    )
    assert result.verdict == REJECT
    assert any(reason.startswith("room_rental") for reason in result.rejections)


def test_two_bedroom_apartment_is_not_a_room(settings):
    result = evaluate(
        make(
            title="5 1/2 avec 2 chambres à coucher",
            description="Laveuse-sécheuse incluses, frigo et cuisinière fournis",
        ),
        settings,
    )
    assert result.verdict == MATCH


def test_room_exclusion_can_be_waived(settings):
    settings.exclude_rooms = False
    result = evaluate(
        make(
            title="Chambre à louer",
            description="Laveuse-sécheuse, frigo et cuisinière inclus",
        ),
        settings,
    )
    assert result.verdict == MATCH


# --- reason plumbing ------------------------------------------------------


def test_reasons_combine_rejections_and_unknowns(settings):
    result = evaluate(make(price_text="2 500 $/mois", description="Beau logement"), settings)
    assert "over_budget" in result.reasons
    assert "washer_unknown" in result.reasons


def test_attributes_feed_appliance_detection(settings):
    # A three-word description with a structured "Laveuse/Sécheuse: Oui" attribute is
    # extremely common on Kijiji and must count.
    result = evaluate(
        make(
            description="Libre le 1er juillet",
            attributes={"Laveuse/Sécheuse": "Oui", "Réfrigérateur": "Oui", "Cuisinière": "Oui"},
        ),
        settings,
    )
    assert result.verdict == MATCH


@pytest.mark.parametrize("verdict_field", ["price", "place", "appliances", "dwelling"])
def test_evaluation_always_carries_its_parsed_parts(settings, verdict_field):
    # Everything parsed on the way is kept, so a stored row can be re-judged later
    # without re-scraping.
    result = evaluate(make(), settings)
    assert getattr(result, verdict_field) is not None
