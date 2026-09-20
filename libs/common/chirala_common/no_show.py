"""What a no-show costs, said once.

There were two vocabularies. The No-Show Processing screen offered
``one_night_tax``, ``one_night``, ``full_stay`` and ``none``; the night audit
defaulted to ``first_night``, a value the screen never produces and nothing
recognises. They could not agree even in principle.

Worse, only one of the four ever did anything. ``process_no_shows`` branched on
``basis == "none"`` and otherwise charged exactly one night's rate, so a
property that chose "Full Stay Amount" had its guests charged one night and no
error anywhere said so. The setting read as a policy and behaved as an on/off
switch -- the same fault a corporate rate had before it was linked by id, and a
group rate before it was quoted at booking.

So: one tuple, one set of labels, and one function that turns a basis into an
amount, in a module with no dependencies of its own so booking-core and finance
may both import it. What a no-show costs is now the same question wherever it
is asked.
"""

from __future__ import annotations

from decimal import Decimal

#: How a no-show penalty is worked out.
BASES: tuple[str, ...] = ("one_night_tax", "one_night", "full_stay", "none")

LABELS: dict[str, str] = {
    "one_night_tax": "1 Night Room + Tax",
    "one_night": "1 Night Room only",
    "full_stay": "Full Stay Amount",
    "none": "No Penalty",
}

#: What an unconfigured property does.
#:
#: ``none``, deliberately. ``find_no_shows`` states the principle -- "a
#: background job should not take a guest's money while nobody is watching" --
#: and the previous default of one night violated it on every property that had
#: never chosen a policy, which was all of them. A hotel that wants the audit
#: to charge says so; silence is not consent to bill a guest.
DEFAULT_BASIS = "none"

#: The old audit default. Accepted on the way in so rows written before this
#: module existed still mean something, and read as the one night they were
#: actually charged.
LEGACY_ALIASES: dict[str, str] = {"first_night": "one_night_tax"}


def normalise(value: str | None) -> str:
    """Accept what was stored, return a basis this code understands."""
    if not value:
        return DEFAULT_BASIS
    key = value.strip().lower().replace(" ", "_").replace("-", "_")
    key = LEGACY_ALIASES.get(key, key)
    return key if key in BASES else DEFAULT_BASIS


def is_valid(value: str) -> bool:
    key = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return LEGACY_ALIASES.get(key, key) in BASES


def penalty(
    basis: str,
    *,
    nightly_rate: Decimal,
    nights: int,
) -> tuple[Decimal, bool]:
    """The penalty for one room, and whether tax should be added to it.

    Tax is *not* computed here. It comes from ``finance.tax_rules`` through the
    tax engine, and a number invented in this module would disagree with the
    guest's bill -- which is the whole reason the screen takes its figures from
    the engine rather than multiplying by a rate it remembers. So this returns
    the taxable base and a flag saying whether the chosen basis is one that
    carries tax; the caller posts it through the engine either way.

    ``full_stay`` charges every night that was booked, which is what it says
    and what the screen has always shown. Charging one night for it -- the old
    behaviour -- meant a property could not actually hold a guest to the terms
    it had agreed with them.
    """
    key = normalise(basis)
    if key == "none":
        return Decimal("0"), False
    if key == "full_stay":
        return Decimal(str(nightly_rate)) * max(int(nights), 1), True
    # one_night and one_night_tax both charge a single night; they differ only
    # in whether tax rides on top.
    return Decimal(str(nightly_rate)), key == "one_night_tax"
