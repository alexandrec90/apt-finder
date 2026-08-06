"""Text folding — the shared first step of every other parser here.

Québec listings are written by hand, on phones, in two languages, by people who are not
being careful. In one afternoon's sample you will see `Gatineau`, `GATINEAU`, `gatineau`,
`Gâtineau`, `laveuse`/`Laveuse`/`LAVEUSE`, `sécheuse`/`secheuse`, straight and curly
apostrophes, and a non-breaking space wherever French typography wants one (`1 450 $` is
*three* codepoints of space you did not expect).

Folding all of that away once, up front, means every downstream matcher can be written
against plain lowercase ASCII-ish text and stay readable. The alternative — accent and
punctuation alternatives inside every pattern — is unreadable and gets one wrong
eventually.

Note the character tables below are built from codepoints rather than pasted literals.
Half of these characters are invisible or near-identical to ASCII in a diff, so a
literal table is unreviewable — and ruff's RUF001 rejects ambiguous characters in source
for exactly that reason.
"""

import re
import unicodedata

__all__ = ["contains_any", "find_any", "fold"]

#: Space-like characters French typography uses as digit-group separators and before
#: `$`/`:`/`?`. All of them are invisible in a diff, and `\s` alone does not cover the
#: separator/format ones.
_SPACE_CODEPOINTS = (
    0x00A0,  # NO-BREAK SPACE — the usual thousands separator
    0x202F,  # NARROW NO-BREAK SPACE — before `$`, `:`, `?`
    0x2009,  # THIN SPACE
    0x2007,  # FIGURE SPACE
    0x200A,  # HAIR SPACE
    0x2028,  # LINE SEPARATOR
    0x2029,  # PARAGRAPH SEPARATOR
)
_SPACE_CHARS = "".join(chr(code) for code in _SPACE_CODEPOINTS)
_SPACE_RE = re.compile(f"[{re.escape(_SPACE_CHARS)}\\s]+")

#: Apostrophe variants, so `l'unite` folds the same however the apostrophe was typed.
_APOSTROPHES = "".join(
    chr(code)
    for code in (
        0x2018,  # LEFT SINGLE QUOTATION MARK
        0x2019,  # RIGHT SINGLE QUOTATION MARK — what phone keyboards produce
        0x02BC,  # MODIFIER LETTER APOSTROPHE
        0x00B4,  # ACUTE ACCENT
        0x0060,  # GRAVE ACCENT
    )
)

#: Dash variants, so `laveuse-secheuse` matches however the dash was typed.
_DASHES = "".join(
    chr(code)
    for code in (
        0x2010,  # HYPHEN
        0x2011,  # NON-BREAKING HYPHEN
        0x2012,  # FIGURE DASH
        0x2013,  # EN DASH
        0x2014,  # EM DASH
        0x2015,  # HORIZONTAL BAR
        0x2212,  # MINUS SIGN
    )
)

#: U+2044 FRACTION SLASH. NFKC expands the vulgar fraction one-half into `1`, this
#: character, `2` — not an ASCII `/`. It is invisible in output and matches no ordinary
#: regex, so folding it to `/` is what collapses the two ways Québec room counts get
#: typed (`4` + one-half, and `4 1/2`) into one form the dwelling parser reads with a
#: single pattern.
_FRACTION_SLASH = chr(0x2044)


def fold(value: str | None) -> str:
    """Lowercase, strip accents, normalise punctuation and whitespace.

    ``fold("Gâtineau — 4<half>,  LAVEUSE/SÉCHEUSE incluses")`` becomes
    ``"gatineau - 41/2, laveuse/secheuse incluses"``.

    Vulgar fractions do not survive as single characters: NFKC expands one-half into
    `1`, a fraction slash, `2`, and that slash is then folded to `/`. That is the useful
    outcome — both room-count spellings end up in the same shape. Plain `/` survives
    throughout, which `laveuse/secheuse` also depends on.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    for char in _APOSTROPHES:
        text = text.replace(char, "'")
    for char in _DASHES:
        text = text.replace(char, "-")
    text = text.replace(_FRACTION_SLASH, "/")
    # NFD splits `é` into `e` + combining acute; dropping category Mn removes the accent
    # while leaving digits and letters intact.
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    return _SPACE_RE.sub(" ", text).strip()


def contains_any(folded: str, needles: tuple[str, ...]) -> bool:
    """True if any needle appears in already-folded text."""
    return any(needle in folded for needle in needles)


def find_any(folded: str, needles: tuple[str, ...]) -> str | None:
    """The first needle that appears, or None. Used to report *why* a rule fired."""
    for needle in needles:
        if needle in folded:
            return needle
    return None
