"""Rent parsing, including the numbers that only look like rent."""

from decimal import Decimal

import pytest

from apt_finder.normalize.price import parse_price

NBSP = chr(0x00A0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1450$", Decimal("1450")),
        ("1450 $", Decimal("1450")),
        ("$1450", Decimal("1450")),
        (f"1{NBSP}450 $", Decimal("1450")),  # French thousands separator
        ("1,450$", Decimal("1450")),  # anglophone thousands separator
        ("1 450,00 $", Decimal("1450.00")),  # French decimal comma
        ("$1,450.00", Decimal("1450.00")),
        ("1450 CAD", Decimal("1450")),
        ("1450 dollars", Decimal("1450")),
        ("CA$1,450", Decimal("1450")),  # Facebook's rendering
    ],
)
def test_parses_every_common_format(text, expected):
    price = parse_price(text)
    assert price is not None
    assert price.amount == expected


def test_french_decimal_and_english_thousands_are_distinguished():
    # The whole point of the last-separator rule: these are the same number written
    # two ways, and a naive `replace(",", "")` gets one of them 100x wrong.
    assert parse_price("1 450,00 $").monthly == Decimal("1450.00")
    assert parse_price("1,450 $").monthly == Decimal("1450.00")


def test_monthly_period_detected():
    for text in ("1450 $/mois", "1450 $ par mois", "1450$ mensuel", "$1450/month"):
        price = parse_price(text)
        assert price.period == "month", text
        assert price.monthly == Decimal("1450.00")


def test_weekly_is_normalised_and_flagged():
    price = parse_price("650 $/semaine")
    assert price.period == "week"
    assert price.is_short_term
    # 650 * 52/12 — nowhere near a $650 month, which is the point.
    assert price.monthly == Decimal("2816.67")


def test_nightly_is_flagged_short_term():
    price = parse_price("95 $ par nuit")
    assert price.period == "day"
    assert price.is_short_term


def test_price_on_request_returns_none():
    for text in ("Prix sur demande", "À discuter", "Please Contact"):
        assert parse_price(text) is None, text


def test_room_count_is_not_a_price():
    # "3 1/2" has no currency marker, so it must not be read as $3 or $312.
    assert parse_price("Beau 3 1/2 rénové") is None


def test_floor_area_is_not_a_price():
    assert parse_price("Grand logement de 1200 pi2") is None


def test_phone_number_is_not_a_price():
    assert parse_price("Appelez au 819-555-0123") is None


def test_largest_currency_figure_wins():
    # Descriptions list add-ons; the rent is the big number, not the first one.
    price = parse_price("Stationnement 75 $, chauffage 50 $/mois, loyer 1 395 $/mois")
    assert price.amount == Decimal("1395")


def test_sources_are_tried_in_order():
    # A provider's own price field must not be overridden by prose in the description.
    price = parse_price("1 500 $", "titre sans prix", "chauffage 50 $ en sus")
    assert price.amount == Decimal("1500")


def test_falls_through_empty_sources():
    price = parse_price(None, "", "Loyer 1 250 $/mois")
    assert price.amount == Decimal("1250")


def test_zero_and_negative_are_rejected():
    assert parse_price("0 $") is None


def test_no_price_anywhere():
    assert parse_price("Superbe appartement rénové") is None


def test_rent_word_fallback_without_currency_marker():
    price = parse_price("Loyer: 1425 par mois")
    assert price is not None
    assert price.amount == Decimal("1425")
    assert price.period == "month"
