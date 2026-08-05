"""Folding: the step every other parser depends on.

The characters under test are invisible or near-invisible in a diff, so they are built
with `chr()` rather than pasted in. That is deliberate on two counts: a literal NBSP in
a test file is indistinguishable from a space to every reviewer, and ruff's RUF001 is
right to reject ambiguous characters in source.
"""

from apt_finder.normalize.text import contains_any, find_any, fold

NBSP = chr(0x00A0)  # French thousands separator: "1 450"
NARROW_NBSP = chr(0x202F)  # French, before `$` and `:`
THIN_SPACE = chr(0x2009)
CURLY_APOSTROPHE = chr(0x2019)  # as in "l'unite", typed on a phone
EN_DASH = chr(0x2013)
VULGAR_HALF = chr(0x00BD)  # "4½" — Québec room notation


def test_strips_accents_and_lowercases():
    assert fold("Sécheuse INCLUSE à Gatineau") == "secheuse incluse a gatineau"


def test_normalizes_the_three_french_spaces():
    raw = f"1{NBSP}450{NARROW_NBSP}${THIN_SPACE}/ mois"
    assert fold(raw) == "1 450 $ / mois"


def test_normalizes_curly_apostrophes():
    assert fold(f"dans l{CURLY_APOSTROPHE}unité") == fold("dans l'unite") == "dans l'unite"


def test_normalizes_dashes():
    assert fold(f"laveuse{EN_DASH}secheuse") == "laveuse-secheuse"


def test_expands_vulgar_fractions_to_ascii():
    # NFKC turns `½` into `1` + U+2044 + `2`; folding that slash to ASCII is what makes
    # "4½" and "4 1/2" one shape. dwelling.py's room-count regex depends on it, so this
    # is the test that fails if the fraction-slash step is ever dropped.
    assert fold(f"Beau 4{VULGAR_HALF} rénové") == "beau 41/2 renove"


def test_both_room_notations_fold_compatibly():
    assert "1/2" in fold(f"4{VULGAR_HALF}")
    assert "1/2" in fold("4 1/2")


def test_handles_none_and_empty():
    assert fold(None) == ""
    assert fold("") == ""


def test_collapses_runs_of_whitespace():
    assert fold("  laveuse \n\t  secheuse  ") == "laveuse secheuse"


def test_contains_any_and_find_any():
    folded = fold(f"Grand 4{VULGAR_HALF} avec laveuse")
    assert contains_any(folded, ("laveuse", "secheuse"))
    assert not contains_any(folded, ("frigo",))
    assert find_any(folded, ("secheuse", "laveuse")) == "laveuse"
    assert find_any(folded, ("frigo",)) is None
