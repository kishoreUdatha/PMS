"""Applying the rules from screen 118 to a charge.

Screen 118 stores what the property charges. This is where those rules meet a
bill: given a charge, its date and what kind of charge it is, resolve the rules
in force and work out the tax.

Three things matter here.

**The rate is snapshotted, not referenced.** ``folio_entry_taxes`` records the
rate that was used, so a bill from last month keeps saying 5% after the rule is
raised to 8%. That is why 118 keeps revisions instead of overwriting, and it is
the whole reason the two halves exist.

**Exclusive tax is money on the bill.** The folio balance is
``SUM(debits) - SUM(credits)``, so an exclusive tax has to be a debit of its own
or the guest is never asked to pay it. Inclusive tax is already inside the
charge, so it is recorded but posts nothing extra.

**Rounding happens once per component.** Each component is rounded to paise and
the posted tax line is the sum of those rounded parts, so the breakdown always
adds up to the line on the bill.

**It lives in the shared library** because booking-core posts charges too --
a cancellation fee, a no-show penalty, a room upgrade -- inside its own
transactions. It used to write those rows raw, untaxed, so the same kind of
charge was taxed when finance posted it and not when the desk did. One engine,
imported by both, is the only way the two cannot drift apart again.
``finance_service.tax_engine`` re-exports everything here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from chirala_common import charge_types
from sqlalchemy import text
from sqlalchemy.orm import Session

PAISE = Decimal("0.01")

# What a charge is, for matching against a rule's applicability.
TAX_CATEGORIES = (
    "rooms", "spa", "fnb_outlets", "banquets_events", "in_room_dining",
    "other_services",
)

# Which category a charge belongs to when the caller does not say.
#
# Served from chirala_common.charge_types, which carries the label and the
# category together. They used to be separate lists: a type could be billable
# and describable while missing from the mapping here, and the charge then
# posted untaxed with nothing saying so. "breakfast" and "activity" were both
# in that state.
#
# Anything still unrecognised posts untaxed rather than being guessed at,
# because guessing wrong means taxing a guest incorrectly. A property whose
# departments do not line up with these should map its own charge codes.
SOURCE_CATEGORY = dict(charge_types.SOURCE_CATEGORY)


@dataclass
class TaxLine:
    """One component of tax on one charge."""

    tax_code: str
    rate_snapshot: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal
    #: 'percent' or 'amount'. Without it a flat 20-rupee cess and a 20 per
    #: cent tax are indistinguishable downstream, and a bill that reads
    #: "BEACH_CESS (20%)" for a 120-rupee line is simply wrong.
    rate_type: str = "percent"
    #: 'inclusive' when this tax is already inside the charge it sits on,
    #: 'exclusive' when it was added as its own debit. Without it, nothing
    #: downstream can tell whether reversing the charge also reverses the tax
    #: -- and an adjustment credited both, refunding inclusive tax twice.
    apply_as: str = "exclusive"


@dataclass
class TaxResult:
    lines: list[TaxLine] = field(default_factory=list)
    #: Added to the bill. Zero when every applicable rule is inclusive.
    exclusive_total: Decimal = Decimal("0")
    #: Already inside the charge; recorded, never posted again.
    inclusive_total: Decimal = Decimal("0")

    @property
    def any_tax(self) -> bool:
        return bool(self.lines)


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(PAISE, rounding=ROUND_HALF_UP)


def resolve_rules(
    session: Session,
    *,
    property_id: uuid.UUID,
    category: str,
    on_date: date,
    tax_group_id: uuid.UUID | None = None,
) -> list[dict]:
    """The rules that apply to one charge.

    One revision per code: the newest whose effective window has opened and not
    closed. A rule that is inactive, not yet started or already ended does not
    apply — which is what makes "Schedule Change" work without a cutover job.

    Tax groups are **alternatives**, not additions: a charge pays one GST rate.
    Exactly one is returned — the group the charge names, else the one marked
    default for this area. Service charges and other levies do stack, so every
    matching one is returned.
    """
    rows = session.execute(
        text(
            """
            SELECT DISTINCT ON (t.code)
                   t.id, t.code, t.name, t.charge_type, t.rate_type, t.rate_value,
                   t.amount_basis, t.apply_as, t.calculation_rule, t.is_default
            FROM finance.tax_rules t
            WHERE t.property_id = :prop
              AND t.status = 'active'
              -- GST components carry no rate of their own; the group that
              -- contains them supplies it.
              AND t.charge_type <> 'gst_component'
              AND :cat = ANY(t.applicability)
              AND t.effective_from <= :on_date
              AND (t.effective_to IS NULL OR t.effective_to >= :on_date)
            ORDER BY t.code, t.effective_from DESC, t.revision DESC
            """
        ),
        {"prop": property_id, "cat": category, "on_date": on_date},
    ).mappings().all()

    groups = [dict(r) for r in rows if r["charge_type"] == "tax_group"]
    stacking = [dict(r) for r in rows if r["charge_type"] != "tax_group"]

    chosen: list[dict] = []
    if tax_group_id is not None:
        chosen = [g for g in groups if g["id"] == tax_group_id]
    if not chosen:
        defaults = [g for g in groups if g["is_default"]]
        # Exactly one default is the configuration this expects. With none, or
        # more than one, no group is applied: over-charging a guest is worse
        # than a missing line someone can see and fix.
        chosen = defaults if len(defaults) == 1 else []

    return chosen + stacking


#: How many of a thing a flat levy is charged for, by the basis its rule
#: declares. Anything unrecognised falls back to ``units``, which is what the
#: engine did for every basis before it read this column at all.
def _quantity(basis: str | None, *, units: int, nights: int | None,
              guests: int | None) -> int:
    if basis == "per_stay":
        # Once for the booking, however long it runs.
        return 1
    if basis == "per_night":
        return nights if nights is not None else units
    if basis == "per_person":
        # Unknown occupancy charges for one rather than inventing a number:
        # under-charging is visible on the bill, over-charging is a refund.
        return guests if guests is not None else 1
    return units


def compute_tax(
    rules: list[dict], *, amount: Decimal, units: int = 1,
    nights: int | None = None, guests: int | None = None,
) -> TaxResult:
    """Work out the tax on one charge.

    A percentage ignores all of these. A flat levy is multiplied by whichever
    quantity its own ``amount_basis`` names -- per night, per person, per stay
    or per unit -- so pass what you know and let the rule choose. Passing only
    ``units`` keeps the old behaviour for per-night and per-unit rules.

    This used to multiply every flat rule by ``units`` whatever its basis
    said, so a per-stay levy was charged once per night and a per-person one
    was charged per night as well.
    """
    result = TaxResult()
    if amount <= 0 or not rules:
        return result

    for rule in rules:
        inclusive = rule["apply_as"] == "inclusive"
        components = (rule["calculation_rule"] or {}).get("components") or []

        if rule["rate_type"] == "amount":
            # A flat levy is not a share of the price, so it cannot sensibly be
            # "included" in it; it is always added.
            qty = _quantity(rule.get("amount_basis"), units=units,
                            nights=nights, guests=guests)
            tax = _money(Decimal(rule["rate_value"]) * qty)
            if tax > 0:
                result.lines.append(
                    TaxLine(rule["code"], Decimal(rule["rate_value"]), amount,
                            tax, rate_type="amount",
                            # A flat levy is always added; see above.
                            apply_as="exclusive")
                )
                result.exclusive_total += tax
            continue

        headline = Decimal(rule["rate_value"])
        if headline <= 0:
            continue

        # An inclusive rate is already inside the amount, so the taxable base is
        # the amount net of it: 6500 at 5% inclusive is 6190.48 + 309.52.
        taxable = (
            _money(amount / (1 + headline / Decimal(100))) if inclusive else amount
        )

        # A group splits into its components; anything else is a single line.
        parts = [p for p in (components
                             or [{"code": rule["code"], "rate": headline}])
                 if Decimal(str(p["rate"])) > 0]

        # Inclusive tax is what is left of the price once the taxable base is
        # taken out, and the components share exactly that. Rounding each
        # component from the base independently -- 6190.48 at 2.5% twice is
        # 154.76 + 154.76 -- gave 6500.00 back as 6190.48 + 309.52 only by
        # luck; at other prices the parts came to a paisa more or less than
        # the price the guest was quoted, and an invoice whose lines do not
        # add up to its total is one an auditor will not accept. So the parts
        # are apportioned by rate and the last absorbs the rounding.
        remaining = amount - taxable if inclusive else Decimal("0")
        weight = sum((Decimal(str(p["rate"])) for p in parts), Decimal("0"))
        for i, part in enumerate(parts):
            rate = Decimal(str(part["rate"]))
            if not inclusive:
                tax = _money(taxable * rate / Decimal(100))
            elif i == len(parts) - 1:
                tax = remaining
            else:
                tax = _money((amount - taxable) * rate / weight)
                remaining -= tax
            if tax <= 0:
                continue
            result.lines.append(TaxLine(
                part["code"], rate, taxable, tax,
                # Carried onto the line so a later reversal knows whether this
                # tax was inside the charge or posted beside it.
                apply_as="inclusive" if inclusive else "exclusive"))
            if inclusive:
                result.inclusive_total += tax
            else:
                result.exclusive_total += tax

    return result


def record_tax_lines(
    session: Session, *, folio_entry_id: uuid.UUID, result: TaxResult
) -> None:
    """Store the breakdown against the charge it was computed on."""
    for line in result.lines:
        session.execute(
            text(
                """
                INSERT INTO finance.folio_entry_taxes
                    (folio_entry_id, tax_code, rate_snapshot, taxable_amount,
                     tax_amount, apply_as)
                VALUES (:eid, :code, :rate, :taxable, :tax, :apply_as)
                """
            ),
            {
                "eid": folio_entry_id, "code": line.tax_code,
                "rate": line.rate_snapshot, "taxable": line.taxable_amount,
                "tax": line.tax_amount, "apply_as": line.apply_as,
            },
        )


def tax_breakdown(session: Session, folio_id: uuid.UUID) -> list[dict]:
    """Tax on a folio, grouped by code — what a bill shows under the total."""
    rows = session.execute(
        text(
            """
            SELECT fet.tax_code,
                   fet.rate_snapshot,
                   sum(fet.taxable_amount) AS taxable_amount,
                   sum(fet.tax_amount)     AS tax_amount
            FROM finance.folio_entry_taxes fet
            JOIN finance.folio_entries fe ON fe.id = fet.folio_entry_id
            WHERE fe.folio_id = :fid AND fe.reversal_of_id IS NULL
            GROUP BY fet.tax_code, fet.rate_snapshot
            ORDER BY fet.tax_code
            """
        ),
        {"fid": folio_id},
    ).mappings().all()
    return [dict(r) for r in rows]
