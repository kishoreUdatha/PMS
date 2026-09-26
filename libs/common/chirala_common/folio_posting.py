"""Posting a charge to a folio: the one implementation every service uses.

Finance's ledger and booking-core both put charges on folios -- the night
audit's room nights on one side; cancellation fees, no-show penalties and room
upgrades on the other. booking-core used to write its rows straight into
``finance.folio_entries``: untaxed, stamped with ``CURRENT_DATE`` (UTC, and
the calendar rather than the ledger's open day), and in whatever currency the
caller happened to hold. The same cancellation fee was therefore taxed when
finance posted it and not when the desk did, and could land on a business
date the night audit had already closed.

Calling finance over HTTP was considered and rejected: the posting would
commit in finance's transaction while the booking change it belongs to
commits (or rolls back) in booking-core's, and a fee charged for a
cancellation that then failed -- or a cancellation whose fee silently did not
post -- is worse than either bug. So the posting logic lives here, and both
services run it inside their own transaction. ``finance_service.ledger``'s
``post_charge`` is a thin wrapper that turns :class:`PostingError` into its
own ``LedgerError``.

SIGN CONVENTION: a charge is a DEBIT (increases what the guest owes).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from .tax_engine import (
    SOURCE_CATEGORY,
    compute_tax,
    record_tax_lines,
    resolve_rules,
)


class PostingError(Exception):
    """The posting cannot be made as asked. ``conflict`` maps to a 409."""

    def __init__(self, message: str, *, conflict: bool = False) -> None:
        self.conflict = conflict
        super().__init__(message)


@dataclass
class EntryResult:
    entry_id: uuid.UUID
    created: bool


def resolve_currency(
    session: Session, *, stated: str | None, folio_ids=(), payment_id=None,
) -> str:
    """The currency a posting is in: the one its folio (or payment) keeps.

    Every posting function used to default ``currency`` to ``"INR"``, and the
    night audit -- the busiest writer of all -- never passed one. So a property
    selling rooms in dollars had every room night written as rupees: the
    number was right, the unit beside it was not, and every report that groups
    by currency split one folio's money into two piles.

    A folio is opened in its reservation's currency and a payment records the
    currency it was captured in, so the currency is already known wherever
    money lands. Taking it from there means a caller can no longer get it
    wrong by omission. A caller that *states* one that disagrees is refused:
    mixing units on one folio makes its balance meaningless, and there is no
    conversion here to make it mean anything.
    """
    known: set[str] = set()
    if folio_ids:
        known |= {
            r for r in session.execute(
                text("SELECT DISTINCT currency FROM finance.folios "
                     "WHERE id = ANY(:ids)"),
                {"ids": list(folio_ids)},
            ).scalars()
            if r
        }
    if payment_id is not None:
        cur = session.execute(
            text("SELECT currency FROM finance.payments WHERE id = :id"),
            {"id": payment_id},
        ).scalar()
        if cur:
            known.add(cur)
    if len(known) > 1:
        raise PostingError(
            "These folios are kept in different currencies ("
            + ", ".join(sorted(known))
            + "); one posting cannot be split across them."
        )
    if stated and known and stated.upper() != next(iter(known)).upper():
        raise PostingError(
            f"This folio is kept in {next(iter(known))}; an amount in "
            f"{stated} cannot be posted to it."
        )
    if known:
        return next(iter(known))
    # Nothing to take it from (a folio id that does not exist fails on its
    # foreign key a moment later anyway). The stated currency, or the
    # historical default, rather than inventing a third answer.
    return stated or "INR"


def post_charge(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    folio_id: uuid.UUID,
    amount: Decimal,
    business_date: date,
    source_type: str,
    source_line_key: str,
    charge_code_id: uuid.UUID | None = None,
    source_id: str | None = None,
    currency: str | None = None,
    tax_category: str | None = None,
    tax_units: int = 1,
    #: False posts the amount untaxed whatever its category says -- for a
    #: charge the guest was explicitly quoted without tax, such as the no-show
    #: screen's "1 night room only".
    taxed: bool = True,
    note: str | None = None,
    quantity: Decimal | None = None,
    unit_amount: Decimal | None = None,
    discount_amount: Decimal | None = None,
    posted_by: str | None = None,
) -> EntryResult:
    """Post an immutable debit, with the tax the rules require.

    Tax is applied when the charge says what kind of charge it is, either via
    ``tax_category`` or a ``source_type`` that maps to one unambiguously. An
    unrecognised source posts untaxed rather than guessing a category, because
    guessing wrong means billing a guest the wrong amount.

    The tax breakdown is written against this entry, and any *exclusive* tax is
    posted as a second debit so the folio balance actually includes it. That
    second entry derives its line key from this one, so a replayed post stays
    idempotent for both.

    Idempotent on (folio, source_type, source_line_key): a duplicate post is a
    no-op returning the existing entry.
    """
    if amount <= 0:
        raise PostingError("Charge amount must be positive")
    currency = resolve_currency(session, stated=currency, folio_ids=[folio_id])

    existing = session.execute(
        text(
            """
            SELECT id FROM finance.folio_entries
            WHERE folio_id = :fid AND source_type = :st AND source_line_key = :slk
            """
        ),
        {"fid": folio_id, "st": source_type, "slk": source_line_key},
    ).first()
    if existing is not None:
        return EntryResult(entry_id=existing.id, created=False)

    entry_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO finance.folio_entries
                (id, organization_id, property_id, folio_id, entry_type, amount,
                 currency, business_date, charge_code_id, source_type, source_id,
                 source_line_key, note, quantity, unit_amount, discount_amount,
                 posted_by)
            VALUES (:id, :org, :prop, :fid, 'debit', :amt, :cur, :bd, :cc, :st,
                    :sid, :slk, :note, :qty, :unit, :disc, :by)
            """
        ),
        {
            "id": entry_id,
            "org": organization_id,
            "prop": property_id,
            "fid": folio_id,
            "amt": amount,
            "note": (note or "").strip() or None,
            "qty": quantity,
            "unit": unit_amount,
            "disc": discount_amount,
            "cur": currency,
            "bd": business_date,
            "cc": charge_code_id,
            "st": source_type,
            "sid": source_id,
            "slk": source_line_key,
            # Who decided this. Null where nobody did -- the night audit
            # charging a room because the clock passed midnight is the system,
            # not a person, and saying so is more honest than attributing it to
            # whoever happened to be signed in.
            "by": posted_by,
        },
    )

    category = (tax_category or SOURCE_CATEGORY.get(source_type)) if taxed \
        else None
    if category:
        # A charge code may name the tax group it belongs to; otherwise the
        # area's default group applies.
        group_id = None
        if charge_code_id is not None:
            group_id = session.execute(
                text(
                    "SELECT tax_rule_id FROM finance.charge_codes WHERE id = :id"
                ),
                {"id": charge_code_id},
            ).scalar_one_or_none()
        rules = resolve_rules(
            session, property_id=property_id, category=category,
            on_date=business_date, tax_group_id=group_id,
        )
        # ``tax_units`` is the nights this charge covers -- the night audit
        # posts one night at a time, so it is 1 there. Named as nights so a
        # per-stay rule is not multiplied by it.
        tax = compute_tax(rules, amount=amount, units=tax_units,
                          nights=tax_units)
        if tax.any_tax:
            record_tax_lines(session, folio_entry_id=entry_id, result=tax)
        if tax.exclusive_total > 0:
            # Its own debit, or the balance would never include it.
            session.execute(
                text(
                    """
                    INSERT INTO finance.folio_entries
                        (organization_id, property_id, folio_id, entry_type,
                         amount, currency, business_date, charge_code_id,
                         source_type, source_id, source_line_key, posted_by)
                    VALUES (:org, :prop, :fid, 'debit', :amt, :cur, :bd, :cc,
                            :st, :sid, :slk, :by)
                    """
                ),
                {
                    "org": organization_id, "prop": property_id, "fid": folio_id,
                    "amt": tax.exclusive_total, "cur": currency,
                    "bd": business_date, "cc": charge_code_id,
                    "st": f"{source_type}_tax", "sid": source_id,
                    "slk": f"{source_line_key}#tax", "by": posted_by,
                },
            )
    return EntryResult(entry_id=entry_id, created=True)
