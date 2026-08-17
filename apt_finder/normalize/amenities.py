"""Appliance detection — and the three ways a Québec listing says "no".

Finding the word ``laveuse`` in a description proves almost nothing. These three
phrases all contain it, and only the first one means the washer is included:

- ``laveuse-sécheuse incluses`` — included.
- ``espace pour laveuse-sécheuse`` / ``branchement laveuse-sécheuse`` — there is a
  *hookup*. You supply the machines. This phrasing is extremely common in Québec
  listings and a naive keyword match reads it as a match every time.
- ``buanderie commune au sous-sol`` — coin laundry in the basement, shared with the
  building. Not "included" in any sense the tenant cares about.

So every detection is tri-state — ``present`` / ``absent`` / ``unknown`` — and carries
the phrase that decided it. ``absent`` and ``unknown`` are kept apart deliberately:
"the ad says there is no washer" and "the ad does not mention a washer" should not lead
to the same verdict, because most short ads simply do not mention one.

One more local convention worth encoding: ``électros inclus`` means the *kitchen*
appliances — fridge and stove, sometimes a dishwasher. It does **not** imply laundry.
And ``tout inclus`` is about utilities (heat, electricity, hot water), not appliances
at all, which is why it appears in no term list here.
"""

import re
from dataclasses import dataclass

from apt_finder.normalize.text import fold

__all__ = ["ABSENT", "PRESENT", "UNKNOWN", "Amenity", "Appliances", "detect", "detect_appliances"]

PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"

# Terms are matched on word boundaries, with an optional plural `s`. Both details are
# load-bearing: without the leading boundary, `washer` matches inside `dishwasher` and
# every ad with a dishwasher claims a washing machine.
WASHER_TERMS = ("laveuse", "lave-linge", "washing machine", "washer")
DRYER_TERMS = ("secheuse", "seche-linge", "dryer")
FRIDGE_TERMS = ("refrigerateur", "frigidaire", "frigo", "fridge", "refrigerator")
#: `four` and `range` are deliberately absent. `four` matches inside `fournis`
#: ("électroménagers fournis"), and `range` matches "price range" / "wide range" —
#: both fire on text that says nothing about a stove.
STOVE_TERMS = ("cuisiniere", "poele", "stove", "oven")
DISHWASHER_TERMS = ("lave-vaisselle", "lave vaisselle", "dishwasher")

#: Covers the kitchen pair in one phrase — the usual way a Québec ad states it.
KITCHEN_BUNDLE_TERMS = (
    "electromenagers inclus",
    "electromenagers fournis",
    "electros inclus",
    "electro inclus",
    "electromenager",
    "appliances included",
    "all appliances",
    "appliances provided",
)

#: A hookup is not an appliance. This is the single most important list in the module.
_HOOKUP_MARKERS = (
    "espace pour",
    "espace laveuse",
    "espace buanderie",
    "branchement",
    "raccordement",
    "prise pour",
    "emplacement pour",
    "prevu pour",
    "pret pour",
    "hookup",
    "hook-up",
    "hook up",
    "rough-in",
    "ready for",
    "space for",
)

#: Shared building laundry: real, but not included with the unit.
_SHARED_MARKERS = (
    "commune",
    "commun",
    "partagee",
    "partage",
    "shared",
    "coin laundry",
    "laverie",
    "dans l'immeuble",
    "de l'immeuble",
    "in the building",
    "buanderie au sous-sol",
)

#: `ni` matters: "sans laveuse ni sécheuse" negates the dryer through a conjunction the
#: washer's negation never reaches.
_NEGATION_BEFORE = ("sans", "pas de", "pas d'", "aucun", "aucune", "ni ", "no ", "not ", "without")
_NEGATION_AFTER = (
    "non inclus",
    "non incluse",
    "non fourni",
    "pas inclus",
    "pas incluse",
    "not included",
    "not provided",
    "excluded",
)

#: How much text around a hit counts as its context. Wide enough to catch
#: "laveuse et sécheuse non incluses", narrow enough not to reach the next feature.
_BEFORE = 32
_AFTER = 40

#: Context never crosses one of these. A qualifier belongs to its own clause:
#: "Frigo et cuisinière inclus. Espace pour laveuse-sécheuse." includes the fridge and
#: the stove, and only the laundry is a hookup — but a flat 40-character window reaches
#: past the full stop and marks all four absent. Commas are deliberately *not*
#: delimiters, because "laveuse, sécheuse non incluses" has to bind across one.
#: `/` is deliberately absent too — it joins "laveuse/secheuse" rather than separating
#: clauses, and treating it as a boundary would stop "espace pour laveuse/secheuse"
#: from negating the dryer.
_CLAUSE_DELIMITERS = ".;!?\n|•·"
#: Negation binds tightly. "logement sans balcon, avec laveuse" must not read as a
#: negated washer, so only the few characters immediately before the term count.
_NEGATION_WINDOW = 14


@dataclass(frozen=True)
class Amenity:
    """One appliance's status, plus the phrase that decided it."""

    status: str
    evidence: str | None = None

    @property
    def included(self) -> bool:
        return self.status == PRESENT


@dataclass(frozen=True)
class Appliances:
    washer: Amenity
    dryer: Amenity
    fridge: Amenity
    stove: Amenity
    dishwasher: Amenity

    @property
    def has_laundry_pair(self) -> bool:
        return self.washer.included and self.dryer.included

    @property
    def has_kitchen_pair(self) -> bool:
        return self.fridge.included and self.stove.included


def _pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Boundary-anchored alternation over the terms, tolerating a plural `s`."""
    body = "|".join(re.escape(term) for term in terms)
    return re.compile(rf"(?<![a-z0-9])(?:{body})s?(?![a-z0-9])")


def _clip_before(text: str) -> str:
    """Keep only the part of the leading context in the term's own clause."""
    cut = max(text.rfind(delimiter) for delimiter in _CLAUSE_DELIMITERS)
    return text[cut + 1 :] if cut != -1 else text


def _clip_after(text: str) -> str:
    """Keep only the part of the trailing context in the term's own clause."""
    cuts = [pos for pos in (text.find(d) for d in _CLAUSE_DELIMITERS) if pos != -1]
    return text[: min(cuts)] if cuts else text


def _classify_hit(folded: str, start: int, end: int) -> tuple[str, str | None]:
    """Decide what one keyword occurrence actually claims, from its surroundings."""
    before = _clip_before(folded[max(0, start - _BEFORE) : start])
    after = _clip_after(folded[end : end + _AFTER])

    for marker in _HOOKUP_MARKERS:
        if marker in before or marker in after:
            return ABSENT, f"hookup:{marker}"
    for marker in _SHARED_MARKERS:
        if marker in before or marker in after:
            return ABSENT, f"shared:{marker}"
    for marker in _NEGATION_AFTER:
        if marker in after:
            return ABSENT, f"negated:{marker}"
    tail = before[-_NEGATION_WINDOW:]
    for marker in _NEGATION_BEFORE:
        if marker in tail:
            return ABSENT, f"negated:{marker.strip()}"
    return PRESENT, folded[start:end]


def detect(folded: str, terms: tuple[str, ...]) -> Amenity:
    """Tri-state detection for one appliance over already-folded text.

    A single ``present`` hit wins over any number of ``absent`` ones: an ad saying
    "laveuse-sécheuse incluses; branchement supplémentaire au sous-sol" does include
    them. Absence only stands when *every* mention was negated, shared, or a hookup.
    """
    statuses = [
        _classify_hit(folded, match.start(), match.end())
        for match in _pattern(terms).finditer(folded)
    ]
    if not statuses:
        return Amenity(UNKNOWN)
    for status, evidence in statuses:
        if status == PRESENT:
            return Amenity(PRESENT, evidence)
    status, evidence = statuses[0]
    return Amenity(status, evidence)


def detect_appliances(*sources: str | None) -> Appliances:
    """Detect every tracked appliance across the title, description and attribute text."""
    # Joined with a clause delimiter, not a space. The title and the description are
    # separate statements, and a plain space lets a qualifier at the end of one bind to
    # a term at the start of the next — "…espace pour laveuse-sécheuse" as a title made
    # the description's "Frigo inclus" read as absent.
    folded = " . ".join(fold(source) for source in sources if source)

    washer = detect(folded, WASHER_TERMS)
    dryer = detect(folded, DRYER_TERMS)
    fridge = detect(folded, FRIDGE_TERMS)
    stove = detect(folded, STOVE_TERMS)
    dishwasher = detect(folded, DISHWASHER_TERMS)

    # "électros inclus" backfills the kitchen pair only — never laundry. See the module
    # docstring: locally, the phrase does not cover a washer and dryer.
    bundle = detect(folded, KITCHEN_BUNDLE_TERMS)
    if bundle.status == PRESENT:
        if fridge.status == UNKNOWN:
            fridge = Amenity(PRESENT, f"bundle:{bundle.evidence}")
        if stove.status == UNKNOWN:
            stove = Amenity(PRESENT, f"bundle:{bundle.evidence}")

    return Appliances(washer=washer, dryer=dryer, fridge=fridge, stove=stove, dishwasher=dishwasher)
