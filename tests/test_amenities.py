"""Appliance detection, and the three ways a listing says "no".

The negative cases matter more than the positive ones here. "espace pour
laveuse-sécheuse" is the single most common phrasing in Gatineau listings that a naive
keyword match reads backwards.
"""

import pytest

from apt_finder.normalize.amenities import ABSENT, PRESENT, UNKNOWN, detect_appliances


def test_laundry_pair_included():
    result = detect_appliances("4 1/2 avec laveuse-sécheuse incluses")
    assert result.washer.status == PRESENT
    assert result.dryer.status == PRESENT
    assert result.has_laundry_pair


def test_english_laundry():
    result = detect_appliances("2 bedroom with washer and dryer included")
    assert result.has_laundry_pair


@pytest.mark.parametrize(
    "text",
    [
        "espace pour laveuse-sécheuse",
        "branchement laveuse-sécheuse au sous-sol",
        "prise pour laveuse et sécheuse",
        "emplacement pour laveuse/sécheuse",
        "washer/dryer hookup available",
        "space for washer and dryer",
    ],
)
def test_hookup_is_not_an_appliance(text):
    result = detect_appliances(text)
    assert result.washer.status == ABSENT, text
    assert not result.has_laundry_pair
    assert result.washer.evidence.startswith("hookup:")


@pytest.mark.parametrize(
    "text",
    [
        "buanderie commune au sous-sol",
        "salle de lavage commune",
        "laveuse et sécheuse partagées",
        "shared laundry in the building",
    ],
)
def test_shared_laundry_is_not_included(text):
    result = detect_appliances(text)
    assert not result.has_laundry_pair, text


@pytest.mark.parametrize(
    "text",
    [
        "sans laveuse",
        "laveuse non incluse",
        "pas de laveuse",
        "aucune laveuse",
        "washer not included",
    ],
)
def test_explicit_negation(text):
    assert detect_appliances(text).washer.status == ABSENT, text


def test_negation_carries_across_ni():
    # "sans laveuse ni sécheuse" negates the dryer through a conjunction the washer's
    # own negation never reaches.
    result = detect_appliances("Logement sans laveuse ni sécheuse")
    assert result.washer.status == ABSENT
    assert result.dryer.status == ABSENT


def test_negation_does_not_bleed_across_a_clause():
    # "sans balcon" must not negate the washer three words later.
    result = detect_appliances("Logement sans balcon, avec laveuse et sécheuse")
    assert result.has_laundry_pair


def test_qualifiers_do_not_leak_across_a_sentence():
    # Regression: a flat 40-character context window reached past the full stop, so the
    # "espace pour" belonging to the laundry marked the fridge and stove absent too.
    # Caught end-to-end, not by an earlier unit test — hence this one.
    result = detect_appliances("Frigo et cuisinière inclus. Espace pour laveuse-sécheuse.")
    assert result.fridge.status == PRESENT
    assert result.stove.status == PRESENT
    assert result.washer.status == ABSENT
    assert result.dryer.status == ABSENT


def test_negation_does_not_leak_backwards_across_a_sentence():
    result = detect_appliances("Pas de lave-vaisselle. Laveuse et sécheuse incluses.")
    assert result.has_laundry_pair
    assert result.dishwasher.status == ABSENT


def test_qualifiers_do_not_leak_between_fields():
    # Title and description are separate statements. A hookup mentioned in the title
    # must not negate an appliance the description says is included.
    result = detect_appliances(
        "3 1/2 avec espace pour laveuse-sécheuse",  # title
        "Frigo et cuisinière inclus.",  # description
    )
    assert result.fridge.status == PRESENT
    assert result.stove.status == PRESENT
    assert result.washer.status == ABSENT


def test_hookup_still_binds_across_a_slash():
    # `/` joins the pair rather than separating clauses, so it must not clip context.
    result = detect_appliances("Espace pour laveuse/sécheuse")
    assert result.washer.status == ABSENT
    assert result.dryer.status == ABSENT


def test_present_beats_absent_when_both_appear():
    result = detect_appliances(
        "Laveuse-sécheuse incluses dans l'unité. Branchement supplémentaire au sous-sol."
    )
    assert result.washer.status == PRESENT


def test_dishwasher_does_not_imply_a_washing_machine():
    # `washer` is a substring of `dishwasher`; without word boundaries every listing
    # with a dishwasher would claim a washing machine.
    result = detect_appliances("Condo with dishwasher and fridge")
    assert result.dishwasher.status == PRESENT
    assert result.washer.status == UNKNOWN


def test_fournis_does_not_imply_an_oven():
    # `four` is a substring of `fournis`, so a bare substring search reports a stove on
    # any listing using the verb "fournir". Tested without a bundle phrase, since
    # "électroménagers fournis" legitimately implies a stove by another route.
    result = detect_appliances("Draps et serviettes fournis")
    assert result.stove.status == UNKNOWN


def test_bundle_phrase_still_implies_a_stove():
    # The other half of the pair above: the bundle route must keep working.
    assert detect_appliances("Électroménagers fournis").stove.status == PRESENT


def test_kitchen_bundle_backfills_fridge_and_stove():
    result = detect_appliances("Électroménagers inclus, libre le 1er juillet")
    assert result.fridge.status == PRESENT
    assert result.stove.status == PRESENT
    assert result.has_kitchen_pair


def test_kitchen_bundle_does_not_imply_laundry():
    # Locally, "électros inclus" means fridge and stove. Reading it as laundry is how
    # you end up visiting an apartment with no washer.
    result = detect_appliances("Électroménagers inclus")
    assert result.washer.status == UNKNOWN
    assert result.dryer.status == UNKNOWN


def test_tout_inclus_is_about_utilities_not_appliances():
    # "Tout inclus" means heat/electricity/hot water. It must not backfill anything.
    result = detect_appliances("3 1/2 tout inclus")
    assert result.washer.status == UNKNOWN
    assert result.fridge.status == UNKNOWN


def test_silence_is_unknown_not_absent():
    result = detect_appliances("Beau grand logement rénové au centre-ville")
    for amenity in (result.washer, result.dryer, result.fridge, result.stove):
        assert amenity.status == UNKNOWN


def test_accents_and_case_are_irrelevant():
    for text in ("LAVEUSE ET SÉCHEUSE INCLUSES", "laveuse et secheuse incluses"):
        assert detect_appliances(text).has_laundry_pair, text


def test_plural_forms_match():
    assert detect_appliances("laveuses et sécheuses fournies").has_laundry_pair


def test_reads_across_multiple_sources():
    # Title, description and the structured attribute blob are all searched.
    result = detect_appliances("4 1/2 Gatineau", "Rénové", "Laveuse/Sécheuse Oui")
    assert result.has_laundry_pair
