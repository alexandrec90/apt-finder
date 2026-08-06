"""Room vs. whole apartment, and Québec room notation."""

import pytest

from apt_finder.normalize.dwelling import parse_dwelling, rooms_to_bedrooms

HALF = chr(0x00BD)


@pytest.mark.parametrize(
    ("text", "rooms"),
    [
        ("Beau 4 1/2 rénové", 4.5),
        (f"Beau 4{HALF} rénové", 4.5),
        ("Grand 4-1/2 disponible", 4.5),
        ("3 1/2 au centre-ville", 3.5),
        (f"1{HALF} meublé", 1.5),
        ("5 1/2 avec garage", 5.5),
    ],
)
def test_quebec_room_notation(text, rooms):
    assert parse_dwelling(text).rooms == rooms


def test_room_notation_implies_a_whole_unit():
    dwelling = parse_dwelling("4 1/2 à louer")
    assert dwelling.is_room is False


def test_price_is_not_read_as_a_room_count():
    # "1 450 $" must not become a 450½. The digit guard exists for exactly this.
    assert parse_dwelling("Logement 1 450 $ par mois").rooms is None


def test_bedrooms_derived_from_rooms():
    assert parse_dwelling("4 1/2").bedrooms == 2.0
    assert parse_dwelling("3 1/2").bedrooms == 1.0
    assert parse_dwelling("1 1/2").bedrooms == 0.0


def test_explicit_bedroom_count_wins_over_derivation():
    assert parse_dwelling("5 1/2 avec 3 chambres à coucher").bedrooms == 3.0


@pytest.mark.parametrize(
    "text",
    ["2 chambres", "2 chambres à coucher", "2 cac", "2 bedrooms", "2 bdrm"],
)
def test_bedroom_notations(text):
    assert parse_dwelling(text).bedrooms == 2.0


def test_rooms_to_bedrooms_never_negative():
    assert rooms_to_bedrooms(1.5) == 0.0
    assert rooms_to_bedrooms(2.5) == 0.0


@pytest.mark.parametrize(
    "text",
    [
        "Chambre à louer dans un 5 1/2",
        "Chambre disponible immédiatement",
        "Colocation recherchée",
        "Room for rent near downtown",
        "Looking for a roommate",
        "Shared accommodation available",
        "Maison de chambres",
    ],
)
def test_room_rentals_detected(text):
    assert parse_dwelling(text).is_room is True, text


def test_bedroom_count_is_not_a_room_rental():
    # The single most important false positive to avoid: `chambre` appears in both
    # "chambre à louer" (a room) and "2 chambres à coucher" (a two-bedroom apartment).
    dwelling = parse_dwelling("Superbe 5 1/2 avec 2 chambres à coucher")
    assert dwelling.is_room is False
    assert dwelling.bedrooms == 2.0


def test_room_phrase_beats_the_room_count_it_mentions():
    # "Chambre à louer dans un 5½" describes the host's apartment, not what is offered.
    dwelling = parse_dwelling("Chambre à louer dans un 5 1/2, tout inclus")
    assert dwelling.is_room is True


@pytest.mark.parametrize(
    ("text", "unit_type"),
    [
        ("Appartement à louer", "apartment"),
        ("Beau condo neuf", "condo"),
        ("Studio au centre-ville", "studio"),
        ("Loft industriel", "loft"),
        ("Logement au sous-sol", "basement"),
    ],
)
def test_unit_type(text, unit_type):
    assert parse_dwelling(text).unit_type == unit_type


def test_undecidable_stays_none():
    dwelling = parse_dwelling("Disponible le 1er juillet, contactez-nous")
    assert dwelling.is_room is None
    assert dwelling.rooms is None


def test_pieces_notation():
    assert parse_dwelling("Logement 4 pièces").rooms == 4.0
