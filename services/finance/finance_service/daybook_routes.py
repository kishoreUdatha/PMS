"""The Day Book — every folio movement on a business date, in one list.

This exists because the Cashiering Centre could not answer a question people
kept asking it. That screen lists money that crossed the counter: payments and
refunds, the things a drawer has to account for. It is deliberately narrow,
and it was called "Transactions", so a charge posted to a folio — a Day use,
a restaurant bill — looked like a row the system had lost. It had not; it was
never that screen's subject.

A folio has four kinds of movement and the desk needs all four in one place at
the end of a day: what guests were charged, what they paid, what was handed
back, and what was written off or corrected. That is this screen. It is the
hotel trade's day book, and it reconciles in one line — charges less payments
less refunds less adjustments is the change in what the house is owed.

**Why it reads folio_entries directly.** Cashiering assembles its list from
``payments`` and ``refunds`` and joins the ledger to find each one's business
date. That is the right shape for a till: the payment row is the subject and
the entry is a detail. Here the entry IS the subject — it is the only table
that has every movement, already carries the business date the night audit
decided, and cannot disagree with the folio balance because it is what the
balance is computed from. Nothing is unioned, so nothing can drift.

**Why the business date and not the clock.** The same rule as everywhere else
in finance. A charge posted at 09:50 on the 19th while the property is still
trading the 18th belongs to the 18th, and a day book that sorted by wall clock
would show it on a day whose totals do not include it.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import date

from chirala_common import charge_types
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .routes import _trading_day
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

daybook_router = APIRouter(prefix="/daybook", tags=["daybook"],
                           route_class=TransactionalRoute)

#: What each movement is, in the words a hotel uses. Derived from
#: ``source_type`` rather than stored, because ``source_type`` is already the
#: authority and a second column saying the same thing is a second column that
#: can be wrong.
#:
#: ``refund`` and ``deposit_refund`` are debits, like charges, and telling them
#: apart matters more here than anywhere: a day book that counted a 23,000
#: refund as a charge would report a day's trading that never happened. The
#: same confusion has already been fixed twice in this service, on the
#: adjustment screen and the reservation payload, both times after the number
#: on screen was believed.
_KIND_SQL = f"""
    CASE
        WHEN e.source_type = 'payment' THEN 'payment'
        WHEN e.source_type = ANY (ARRAY{list(charge_types.REFUND_SOURCES)!r})
            THEN 'refund'
        WHEN e.source_type = 'adjustment' THEN 'adjustment'
        WHEN e.source_type = 'security_deposit' THEN 'deposit'
        ELSE 'charge'
    END
"""

KIND_LABELS = {
    "charge": "Charge",
    "payment": "Payment",
    "refund": "Refund",
    "adjustment": "Adjustment",
    "deposit": "Deposit",
}
KINDS = tuple(KIND_LABELS)


class Movement(BaseModel):
    entry_id: uuid.UUID
    posted_at: str
    business_date: date
    folio_id: uuid.UUID
    folio_no: str | None = None
    reservation_id: uuid.UUID | None = None
    reservation_number: str | None = None
    guest_name: str | None = None
    room_code: str | None = None
    kind: str
    kind_label: str
    source_type: str
    description: str
    note: str | None = None
    entry_type: str
    amount: Decimal
    #: One of these is null on every row. A day book is read as two money
    #: columns, and deriving them in the client means every consumer deriving
    #: them the same way or the page not adding up.
    debit: Decimal | None = None
    credit: Decimal | None = None
    posted_by: str | None = None
    #: A reversing entry and the entry it reverses are both real and both
    #: shown; saying which is which is what stops the pair reading as two
    #: separate mistakes.
    reverses_entry_id: uuid.UUID | None = None


class Totals(BaseModel):
    charges: Decimal = Decimal("0")
    payments: Decimal = Decimal("0")
    refunds: Decimal = Decimal("0")
    adjustments: Decimal = Decimal("0")
    deposits: Decimal = Decimal("0")
    #: Charges less everything that reduced them. The change in what the house
    #: is owed across the day, which is the one figure that ties this screen
    #: to the balance on a folio.
    net: Decimal = Decimal("0")
    count: int = 0


class KindCount(BaseModel):
    value: str
    label: str
    count: int
    total: Decimal


class DayBook(BaseModel):
    business_date: date
    rows: list[Movement]
    totals: Totals
    kinds: list[KindCount]
    rooms: list[str]


#: Everything a person needs to recognise a movement, and nothing that would
#: make the query fan out. The guest reaches a folio only through a
#: reservation, and a folio need not have one — a house account has no guest
#: and no room, so every join here is a LEFT one.
_ROWS_SQL = f"""
    SELECT e.id AS entry_id, e.posted_at, e.business_date,
           e.folio_id, f.folio_no,
           e.entry_type, e.amount, e.source_type, e.note, e.posted_by,
           e.reversal_of_id AS reverses_entry_id,
           {_KIND_SQL} AS kind,
           r.id AS reservation_id, r.number AS reservation_number,
           g.full_name AS guest_name,
           rm.code AS room_code
    FROM finance.folio_entries e
    JOIN finance.folios f ON f.id = e.folio_id
    LEFT JOIN booking.reservations r ON r.id = f.reservation_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    LEFT JOIN LATERAL (
        SELECT rm2.code
        FROM booking.reservation_units ru
        JOIN property.rooms rm2 ON rm2.id = ru.assigned_room_id
        WHERE ru.reservation_id = r.id
        ORDER BY ru.created_at
        LIMIT 1
    ) rm ON TRUE
    WHERE e.property_id = :prop AND e.business_date = CAST(:day AS date)
"""


@daybook_router.get("", response_model=DayBook)
def day_book(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    kind: str | None = Query(None),
    room: str | None = Query(None),
    q: str | None = Query(None, max_length=120),
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Every movement on one business date, newest first.

    The date defaults to the day the property is trading, not the server's
    today — the two differ for most of the night, and for the whole of it in
    a timezone far enough from UTC.

    Filters narrow the rows but never the totals along the top: a day book
    that retotalled itself every time somebody looked at one room's charges
    would stop being a day book. ``kinds`` carries the per-kind figures so the
    filter buttons can show what they will select before they are pressed.
    """
    assert_property_in_org(db, caller, property_id)
    day = on_date or _trading_day(db, property_id)

    where, params = "", {"prop": str(property_id), "day": day}
    if kind in KINDS:
        where += f" AND {_KIND_SQL} = :kind"
        params["kind"] = kind
    if room:
        where += " AND rm.code = :room"
        params["room"] = room
    if q:
        # Folio number, reservation number or guest name — the three things
        # somebody has in front of them when they come to this screen looking
        # for one movement.
        where += (" AND (f.folio_no ILIKE :q OR r.number ILIKE :q"
                  " OR g.full_name ILIKE :q)")
        params["q"] = f"%{q}%"

    rows = db.execute(
        text(f"{_ROWS_SQL}{where} ORDER BY e.posted_at DESC, e.id DESC"),
        params,
    ).mappings().all()

    out: list[Movement] = []
    for e in rows:
        debit = e["amount"] if e["entry_type"] == "debit" else None
        out.append(Movement(
            entry_id=e["entry_id"],
            posted_at=e["posted_at"].isoformat(),
            business_date=e["business_date"],
            folio_id=e["folio_id"], folio_no=e["folio_no"],
            reservation_id=e["reservation_id"],
            reservation_number=e["reservation_number"],
            guest_name=e["guest_name"], room_code=e["room_code"],
            kind=e["kind"], kind_label=KIND_LABELS[e["kind"]],
            source_type=e["source_type"],
            # The payment rows carry no charge type, so the kind is the only
            # honest description; a charge names what it was for.
            description=(KIND_LABELS[e["kind"]] if e["kind"] in
                         ("payment", "refund", "adjustment", "deposit")
                         else charge_types.label(e["source_type"])),
            note=e["note"], entry_type=e["entry_type"],
            amount=e["amount"],
            debit=debit,
            credit=None if debit is not None else e["amount"],
            posted_by=e["posted_by"],
            reverses_entry_id=e["reverses_entry_id"],
        ))

    # Unfiltered, and computed in the database rather than from `out`, so the
    # figures along the top describe the day and not the current filter.
    agg = db.execute(
        text(f"""
            SELECT {_KIND_SQL} AS kind, count(*) AS n,
                   coalesce(sum(e.amount), 0) AS total,
                   -- The same money with its direction kept. A debit raises
                   -- what the house is owed and a credit lowers it, which is
                   -- the only definition of these figures that reconciles
                   -- against a folio balance.
                   coalesce(sum(CASE WHEN e.entry_type = 'debit'
                                     THEN e.amount ELSE -e.amount END), 0)
                     AS signed
              FROM finance.folio_entries e
             WHERE e.property_id = :prop
               AND e.business_date = CAST(:day AS date)
             GROUP BY 1
        """),
        {"prop": str(property_id), "day": day},
    ).mappings().all()
    by_kind = {a["kind"]: a for a in agg}

    # Decimal, like every other money figure this API returns. A float here
    # is a binary approximation of a rupee amount, and the day book is the
    # screen a cashier reconciles a drawer against.
    def tot(k: str) -> Decimal:
        return by_kind[k]["total"] if k in by_kind else Decimal("0")

    def signed(k: str) -> Decimal:
        return by_kind[k]["signed"] if k in by_kind else Decimal("0")

    totals = Totals(
        # Charges, payments, refunds and deposits each only ever go one way,
        # so their magnitude is unambiguous and reads as a hotel says it.
        charges=tot("charge"), payments=tot("payment"),
        refunds=tot("refund"), deposits=tot("deposit"),
        # Adjustments do not. Most reduce a bill, some correct one upwards,
        # and a magnitude would add the two together and call the result a
        # day's allowances -- so this one is signed, negative when the day's
        # adjustments came off the guest's bill on balance.
        adjustments=signed("adjustment"),
        # Not assembled from the figures above: summed straight from the
        # entries, so it cannot disagree with them however the kinds are
        # classified. Debits less credits is the change in what the house is
        # owed, which is what a day book is for.
        net=sum((signed(k) for k in KINDS), Decimal("0")),
        count=sum(int(a["n"]) for a in agg),
    )

    return DayBook(
        business_date=day,
        rows=out,
        totals=totals,
        kinds=[KindCount(value=k, label=KIND_LABELS[k],
                         count=int(by_kind[k]["n"]) if k in by_kind else 0,
                         total=tot(k))
               for k in KINDS],
        # Only rooms that actually moved money today, so the filter cannot
        # offer a room with nothing behind it.
        rooms=[r for (r,) in db.execute(
            text(f"SELECT DISTINCT x.room_code FROM ({_ROWS_SQL}) x "
                 "WHERE x.room_code IS NOT NULL ORDER BY 1"),
            {"prop": str(property_id), "day": day},
        ).all()],
    )
