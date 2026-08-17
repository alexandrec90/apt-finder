"""Rent parsing, in the shapes Québec listings actually use.

The formats in one page of Kijiji results: ``1 450 $``, ``1450$/mois``, ``1 450,00 $``,
``$1,450``, ``1450 CAD par mois``, ``Prix sur demande``. The separator carries meaning
and it is not the English one — ``1 450,00`` is one thousand four hundred fifty, and
``1,450`` is the same number written by an anglophone. Both appear, often on the same
site.

Two traps this module exists to avoid:

- **Numbers that are not prices.** ``3 1/2`` (room count), ``1200 pi2`` (floor area),
  ``819-555-0123`` (phone) are all plausible-looking integers in the same sentence as
  the rent. So a number only counts as a price when it carries a currency marker, or
  sits immediately beside a rent word.
- **Periods that are not months.** ``650 $/semaine`` is not cheap — it is $2,817/month,
  and it is a short-term rental. Normalising to monthly without recording the original
  period would silently rank it as the best match in the list.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from apt_finder.normalize.text import fold

__all__ = ["MONTHLY_MULTIPLIER", "Price", "parse_price"]

#: Weeks and days per month, as Decimals so the conversion never enters float.
MONTHLY_MULTIPLIER: dict[str, Decimal] = {
    "month": Decimal(1),
    "week": Decimal(52) / Decimal(12),
    "day": Decimal(365) / Decimal(12),
}

_PERIOD_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("month", ("mois", "mensuel", "mensuelle", "monthly", "/mo", "par mois", "month")),
    ("week", ("semaine", "hebdomadaire", "weekly", "/wk", "week")),
    ("day", ("nuit", "nuitee", "jour", "nightly", "daily", "par nuit", "night")),
)

#: "Contact me" prices. Worth recognising explicitly so the row is stored with a null
#: price and a `price_unknown` reason, rather than silently failing to parse.
_ON_REQUEST = (
    "prix sur demande",
    "sur demande",
    "a discuter",
    "a negocier",
    "please contact",
    "contact for price",
    "nous contacter",
)

# A number with optional French/English group separators and up to two decimals.
#
# The `+` on the group is load-bearing. With `*`, this branch matches the bare `145` of
# `1450` — `\d{1,3}` takes three digits, the group matches empty, and the alternation is
# satisfied before it ever tries the plain-digits branch. Requiring at least one
# separated group means grouped numbers use the first branch and ungrouped ones fall
# through to the second, which consumes all the digits.
_AMOUNT = r"\d{1,3}(?:[ ,.]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
#: Currency-marked amounts: `$1,450`, `1 450 $`, `1450 cad`. The marker is what
#: separates a rent from a floor area.
_PRICED = re.compile(
    rf"(?:(?P<pre>\$|cad)\s*(?P<a>{_AMOUNT}))|(?:(?P<b>{_AMOUNT})\s*(?P<post>\$|cad\b|dollars?\b))",
    re.IGNORECASE,
)
#: A rent word immediately followed by a bare number — `loyer 1450`, `rent 1450`.
#: Deliberately narrow (no separator allowed but spaces and a colon) because a wider
#: window starts matching the phone number in "loyer 1450, appelez 819 555 0123".
_RENT_WORD = re.compile(rf"(?:loyer|rent|prix|price)\s*:?\s*(?P<a>{_AMOUNT})\b", re.IGNORECASE)

#: How far after the amount to look for "/mois". Long enough for `1 450 $ par mois`,
#: short enough not to reach the next sentence.
_PERIOD_WINDOW = 24


@dataclass(frozen=True)
class Price:
    """A parsed rent. ``monthly`` is what the criteria compare; ``period`` is evidence."""

    amount: Decimal
    period: str  # month | week | day | unknown
    monthly: Decimal

    @property
    def is_short_term(self) -> bool:
        """Quoted by the week or the night — almost always a sublet or a tourist rental."""
        return self.period in ("week", "day")


def _to_decimal(raw: str) -> Decimal | None:
    """Parse a number that may use French or English separators.

    The rule that does the work: whichever of ``.``/``,`` appears **last** is the
    decimal separator, unless it is followed by exactly three digits — in which case it
    is a thousands separator and the number is an integer. That single rule reads
    ``1 450,00``, ``1,450``, ``1450.00`` and ``1.450`` correctly.
    """
    text = raw.replace(" ", "")
    last_sep = max(text.rfind(","), text.rfind("."))
    if last_sep == -1:
        cleaned = text
    else:
        tail = text[last_sep + 1 :]
        if len(tail) == 3:  # thousands group: 1,450 / 1.450
            cleaned = text.replace(",", "").replace(".", "")
        else:  # decimal: 1450,00 / 1450.5
            head = text[:last_sep].replace(",", "").replace(".", "")
            cleaned = f"{head}.{tail}"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _period_after(folded: str, end: int) -> str:
    """Read the period keyword sitting just after an amount, else ``unknown``."""
    window = folded[end : end + _PERIOD_WINDOW]
    for period, words in _PERIOD_WORDS:
        if any(word in window for word in words):
            return period
    return "unknown"


def _candidates(folded: str) -> list[tuple[Decimal, str]]:
    """Every (amount, period) pair the text offers, in order of appearance."""
    found: list[tuple[Decimal, str]] = []
    for match in _PRICED.finditer(folded):
        raw = match.group("a") or match.group("b")
        amount = _to_decimal(raw) if raw else None
        if amount is not None:
            found.append((amount, _period_after(folded, match.end())))
    if not found:
        # Only fall back to bare numbers when nothing carried a currency marker —
        # a page with real prices should never be read through the loose pattern.
        for match in _RENT_WORD.finditer(folded):
            amount = _to_decimal(match.group("a"))
            if amount is not None:
                found.append((amount, _period_after(folded, match.end())))
    return found


def parse_price(*sources: str | None) -> Price | None:
    """Best-effort monthly rent from one or more text sources, in priority order.

    Pass the structured price field first and the free text second:
    ``parse_price(listing.price_label, listing.title, listing.body)``. The first source
    that yields anything wins, so a provider's own price field is never overridden by a
    number mentioned in the description ("chauffage 50 $ / mois en sus").

    Returns ``None`` when there is no price, or the price is "on request".
    """
    for source in sources:
        if not source:
            continue
        folded = fold(source)
        if any(phrase in folded for phrase in _ON_REQUEST):
            return None
        candidates = _candidates(folded)
        if not candidates:
            continue
        # The rent is the largest plausible figure in the field. Descriptions routinely
        # carry smaller add-ons ("stationnement 75 $", "50 $ / mois de plus pour le
        # chauffage"); taking the first match reads those as the rent.
        amount, period = max(candidates, key=lambda pair: pair[0])
        if amount <= 0:
            continue
        multiplier = MONTHLY_MULTIPLIER.get(period, Decimal(1))
        monthly = (amount * multiplier).quantize(Decimal("0.01"))
        return Price(amount=amount, period=period, monthly=monthly)
    return None
