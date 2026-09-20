"""What a desk can bill a guest for, said once.

There were three lists and they did not agree. The Add Charge dialog offered
seven departments; ``ENTRY_LABELS`` in booking-core named nineteen source
types so a folio line could be described; and ``SOURCE_CATEGORY`` in the tax
engine mapped ten of them to a tax category. A source type in the first two
and missing from the third posts **untaxed** -- deliberately, because guessing
a tax category is worse than not applying one -- so the gap was silent. That
is how ``breakfast`` and ``activity`` came to be billable, describable, and
never taxed.

The list was also missing most of what a front desk actually posts by hand.
Early check-in, late check-out, an extra bed, a room upgrade: the fees that
get argued about at the counter, none of them offered.

So one tuple, carrying both the label and the tax category, in a module with
no dependencies of its own. A type here is billable, describable and taxable
by construction -- a new one cannot be added to the dropdown without somebody
deciding what it is for tax, which is the decision that was being skipped.

``category`` is the tax engine's own vocabulary, not a department name. What
rate that category attracts is the property's tax rules to say, and nothing
here presumes it: this file only claims that early check-in is accommodation
and parking is a service, which is true wherever the hotel is.
"""

from __future__ import annotations

from typing import NamedTuple


class ChargeType(NamedTuple):
    value: str
    label: str
    #: The tax engine's category. None means genuinely untaxable here -- a
    #: deposit is not a supply -- rather than "nobody decided".
    category: str | None
    #: Shown in the Add Charge dropdown. Payments, refunds and the audit's own
    #: postings are real source types but nobody bills them by hand.
    billable: bool = True


#: Accommodation. Early check-in and late check-out are the room sold for
#: longer, not a separate service, and are taxed as the room is.
_ROOMS = "rooms"
_FNB = "fnb_outlets"
_IRD = "in_room_dining"
_SERVICES = "other_services"

TYPES: tuple[ChargeType, ...] = (
    # --- the room ---------------------------------------------------------
    ChargeType("early_checkin", "Early check-in", _ROOMS),
    ChargeType("late_checkout", "Late check-out", _ROOMS),
    ChargeType("extra_bed", "Extra bed", _ROOMS),
    ChargeType("extra_person", "Extra person", _ROOMS),
    ChargeType("room_upgrade", "Room upgrade", _ROOMS),
    ChargeType("day_use", "Day use", _ROOMS),
    # --- food and drink ---------------------------------------------------
    ChargeType("restaurant", "Restaurant", _FNB),
    ChargeType("breakfast", "Breakfast", _FNB),
    ChargeType("banquet", "Banquet or event", "banquets_events"),
    ChargeType("in_room_dining", "In-room dining", _IRD),
    # Minibar is F&B consumed in the room, so it follows in-room dining
    # rather than the outlets' service charge.
    ChargeType("minibar", "Mini bar", _IRD),
    # --- services ---------------------------------------------------------
    ChargeType("spa", "Spa", "spa"),
    ChargeType("laundry", "Laundry", _SERVICES),
    ChargeType("transport", "Transport or airport transfer", _SERVICES),
    ChargeType("parking", "Parking", _SERVICES),
    ChargeType("telephone", "Telephone", _SERVICES),
    ChargeType("internet", "Internet", _SERVICES),
    ChargeType("business_centre", "Business centre", _SERVICES),
    ChargeType("activity", "Activity or excursion", _SERVICES),
    ChargeType("pet_fee", "Pet charge", _SERVICES),
    # Recovering the cost of something broken. Whether a property treats that
    # as a supply is its tax rules' business; this says what it is.
    ChargeType("damage", "Damage or breakage", _SERVICES),
    ChargeType("other", "Other", _SERVICES),

    # --- posted by the system, never chosen from a dropdown ----------------
    ChargeType("room_stay", "Room charge", _ROOMS, billable=False),
    ChargeType("room_night", "Room charge", _ROOMS, billable=False),
    ChargeType("room", "Room charge", _ROOMS, billable=False),
    ChargeType("cancellation_fee", "Cancellation fee", _ROOMS, billable=False),
    ChargeType("no_show_penalty", "No-show penalty", _ROOMS, billable=False),
    ChargeType("reservation_change", "Reservation change", None,
               billable=False),
    ChargeType("room_move", "Room move", None, billable=False),
    # A deposit is money held, not a supply, and taxing it would tax the
    # guest twice once it is applied to a real charge.
    ChargeType("security_deposit", "Security deposit", None, billable=False),

    # Moving money between folios on one booking. Never taxed: the tax was
    # settled when the original charge was posted, and taxing the transfer
    # would tax the same supply twice. Category is None for that reason and
    # not because nobody decided.
    ChargeType("folio_transfer_out", "Transferred out", None, billable=False),
    ChargeType("folio_transfer_in", "Transferred in", None, billable=False),
)

BY_VALUE: dict[str, ChargeType] = {t.value: t for t in TYPES}

LABELS: dict[str, str] = {t.value: t.label for t in TYPES}

#: Folio entries that gave money BACK, and are therefore not charges.
#:
#: Both are debits, so every test written as ``entry_type = 'debit'`` sweeps
#: them in -- which is how refunds came to be counted in "Total charges",
#: offered for adjustment on a screen headed Posted Charges, and drawn on the
#: ledger in the same blue as a spa bill.
#:
#: The pair lives here, in the module that already says what a folio line
#: means, because both services need it and they had been keeping separate
#: half-answers: several places tested for 'refund' alone and silently treated
#: a deposit refund as a charge. There are no deposit refunds in this data yet,
#: so nothing was visibly wrong -- which is exactly how it would have reached
#: production.
REFUND_SOURCES: tuple[str, ...] = ("refund", "deposit_refund")

#: What the Add Charge dropdown offers, in the order above -- the room first,
#: because that is what a front desk bills by hand most often.
BILLABLE: tuple[ChargeType, ...] = tuple(t for t in TYPES if t.billable)

#: source type -> tax category, for the engine. Types with no category are
#: absent rather than mapped to None, so the engine's existing "unrecognised
#: posts untaxed" path keeps working unchanged.
SOURCE_CATEGORY: dict[str, str] = {
    t.value: t.category for t in TYPES if t.category
}


def label(value: str | None) -> str:
    """A folio line's description, for a type nobody wrote a note on."""
    if not value:
        return "Entry"
    known = BY_VALUE.get(value)
    return known.label if known else value.replace("_", " ").capitalize()
