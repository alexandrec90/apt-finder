"""Province and proximity — the filter that keeps Ottawa out.

These are the highest-stakes assertions in the suite. Gatineau and Ottawa are one bridge
apart, both marketplaces treat them as one metro, and a listing 4 km away in Ontario
passes every distance check you could write.
"""

import pytest

from apt_finder.config import GATINEAU_LAT, GATINEAU_LON
from apt_finder.normalize.geo import extract_postal_prefix, haversine_km, locate

CENTRE = {"centre_lat": GATINEAU_LAT, "centre_lon": GATINEAU_LON}


@pytest.mark.parametrize(
    ("text", "province"),
    [
        ("Gatineau, QC J8Y 3X5", "QC"),
        ("Ottawa, ON K1A 0B1", "ON"),
        ("Hull J8X 2N9", "QC"),
        ("Nepean K2G 1V8", "ON"),
    ],
)
def test_postal_code_decides_province(text, province):
    assert locate(text, **CENTRE).province == province


def test_postal_code_outranks_a_misleading_city_name():
    # A seller who types "Gatineau" on an Ottawa listing is common. The postal code is
    # the one signal they cannot fake by accident.
    place = locate("Gatineau area, K1S 5B6", **CENTRE)
    assert place.province == "ON"
    assert place.evidence.startswith("postal:K1S")


@pytest.mark.parametrize(
    ("text", "province"),
    [
        ("Gatineau, QC", "QC"),
        ("Aylmer, Québec", "QC"),
        ("Ottawa, ON", "ON"),
        ("Kanata, Ontario", "ON"),
    ],
)
def test_province_token_when_no_postal_code(text, province):
    assert locate(text, **CENTRE).province == province


@pytest.mark.parametrize(
    "city", ["Gatineau", "Hull", "Aylmer", "Buckingham", "Chelsea", "Cantley", "Val-des-Monts"]
)
def test_quebec_municipalities_recognised(city):
    assert locate(city, **CENTRE).province == "QC"


@pytest.mark.parametrize("city", ["Ottawa", "Orléans", "Barrhaven", "Rockland", "Stittsville"])
def test_ontario_municipalities_recognised(city):
    assert locate(city, **CENTRE).province == "ON"


def test_ontario_wins_when_both_appear():
    # Ottawa ads name Gatineau constantly ("minutes to Gatineau"); Gatineau ads rarely
    # name an Ottawa suburb. So a joint mention is much more likely to be an Ontario ad.
    assert locate("Orleans, near Gatineau", **CENTRE).province == "ON"


def test_unknown_location_is_undecided():
    place = locate("Beau logement disponible", **CENTRE)
    assert place.province is None
    assert place.evidence == "none"


def test_empty_location_is_undecided():
    assert locate(None, **CENTRE).province is None


def test_coordinates_produce_a_distance():
    # Aylmer, ~15 km west-southwest of the Hull reference point.
    place = locate("Aylmer, QC", latitude=45.3934, longitude=-75.8491, **CENTRE)
    assert place.distance_km == pytest.approx(14.8, abs=0.5)


def test_distance_is_computed_even_for_ontario():
    # Proximity and province are independent questions: Ottawa is close *and* wrong.
    place = locate("Ottawa, ON", latitude=45.4215, longitude=-75.6972, **CENTRE)
    assert place.province == "ON"
    assert place.distance_km < 10


def test_haversine_known_distance():
    # Gatineau -> Montréal, ~166 km.
    assert haversine_km(45.4765, -75.7013, 45.5017, -73.5673) == pytest.approx(166, abs=5)


def test_haversine_zero():
    assert haversine_km(45.0, -75.0, 45.0, -75.0) == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("gatineau, qc j8y 3x5", "J8Y"),
        ("j8y3x5", "J8Y"),
        ("k1a 0b1", "K1A"),
        ("j8y", None),  # a bare FSA is indistinguishable from a model number
        ("route 148", None),
        ("", None),
    ],
)
def test_postal_prefix_extraction(text, expected):
    assert extract_postal_prefix(text) == expected
