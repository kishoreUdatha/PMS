"""Payment & Cashiering Centre (screen 036).

The ledger already knew how to take money — ``post_payment`` captures a payment
and posts one credit entry per allocation. What was missing was any way to
*look* at what had been taken, by whom, against which folio, and whether the
drawer agrees.

Nothing here invents a second record of money. Every figure on this screen is
read back out of ``finance.payments`` and ``finance.folio_entries``; collecting
a payment calls the same ledger function the deposit screen and checkout call,
and then records the three facts the ledger has no opinion about — who took it,
what reference the guest was given, and which drawer it went into.

**The cash count is the point of a shift.** Card and UPI reconcile themselves;
cash is the one method where the system and the drawer can disagree, and the
only way to find out is to count it. Closing a shift stores what the ledger
expected, what the cashier counted, and the difference. The variance is stored
rather than derived because it is a finding somebody signed off on at the end of
their shift, not a number that changes later if a payment is backdated.

Two things the mockup shows that are not pretended here:

* **Outlets.** There is no POS (SCR-013 / 083) and therefore no outlets. Every
  payment carries the source it genuinely came from — the cashiering desk, a
  deposit collection or a checkout settlement — and the filter offers those.
  ``pos`` exists in the vocabulary and will stay empty until the module is real.
* **Sending a receipt to the guest.** There is no mail or SMS transport in this
  system. The receipt renders and prints; nothing claims it was emailed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import (
    _GRANT_SQL,
    Caller,
    assert_property_in_org,
    build_authz,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from chirala_common import payment_methods
from .database import get_session
from .ledger import Allocation, LedgerError, post_payment
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

cashiering_router = APIRouter(
    prefix="/cashiering", tags=["cashiering"], route_class=TransactionalRoute
)

# One definition, in payment_methods, imported here rather than restated. The
# restating is what let five lists drift apart.
METHODS = payment_methods.METHODS
METHOD_LABELS = payment_methods.LABELS
SOURCES = ("front_desk", "deposit", "checkout", "pos", "night_audit",
           "booking_engine")
SOURCE_LABELS = {
    "front_desk": "Front Desk", "deposit": "Deposit", "checkout": "Checkout",
    "pos": "Restaurant & POS", "night_audit": "Night Audit",
    # Its own source so it can be *excluded* from a drawer count. Money a
    # guest paid a gateway online never passed through anyone's hands here.
    "booking_engine": "Booking Engine",
}
#: Sources with no person behind them. The cashier column is empty for these
#: and that is the truth, not missing data -- worth saying which, because a
#: dash in that column otherwise reads as a record somebody failed to fill in.
UNATTENDED_SOURCES = ("booking_engine",)
NEEDS_REFERENCE = payment_methods.NEEDS_REFERENCE

# What the allocation breakdown calls each kind of charge. Charge codes are the
# intended source (``charge_codes.revenue_category``), but none are configured
# yet — so this also reads the ``source_type`` the ledger already stamps on every
# entry, which says exactly what the charge was for. Grouping everything as
# "Miscellaneous" because a lookup table is empty would throw away information
# the system already has.
CATEGORY_LABELS = {
    # revenue categories, once charge codes are configured
    "room": "Room Charges", "accommodation": "Room Charges",
    "fnb": "Food & Beverage", "food": "Food & Beverage",
    "spa": "Spa & Wellness", "activity": "Activities",
    "transport": "Transport", "tax": "Taxes",
    "misc": "Miscellaneous", "other": "Miscellaneous",
    # entry source types, which is what exists today
    "room_stay": "Room Charges", "room_stay_tax": "Taxes",
    "restaurant": "Food & Beverage", "minibar": "Minibar",
    "cancellation_fee": "Cancellation Fee",
    "no_show_penalty": "No-Show Penalty", "no_show_penalty_tax": "Taxes",
    "reservation_change": "Reservation Change",
    "room_move": "Room Move", "deposit": "Deposit",
    "service": "Services", "adjustment": "Adjustment",
}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class MethodSlice(BaseModel):
    method: str
    label: str
    amount: Decimal
    percent: int
    count: int


class Summary(BaseModel):
    business_date: date
    currency: str
    collected: Decimal
    collected_count: int
    previous_day: Decimal
    change_percent: int | None
    methods: list[MethodSlice]
    outstanding: Decimal
    outstanding_folios: int


class Txn(BaseModel):
    payment_id: uuid.UUID
    received_at: datetime
    guest_name: str | None
    folio_id: uuid.UUID | None
    reservation_number: str | None
    room_code: str | None
    source: str
    source_label: str
    method: str
    method_label: str
    reference: str | None
    amount: Decimal
    currency: str
    cashier: str | None
    payment_status: str
    #: 'payment' or 'refund'. Money out is already negative in `amount`; this
    #: says so in words, so the UI does not have to infer direction from a
    #: sign it might format away.
    kind: str = "payment"


class AllocationRow(BaseModel):
    category: str
    description: str
    amount: Decimal


class PaymentDetail(BaseModel):
    payment_id: uuid.UUID
    received_at: datetime
    guest_name: str | None
    folio_id: uuid.UUID | None
    reservation_id: uuid.UUID | None
    reservation_number: str | None
    room_code: str | None
    method: str
    method_label: str
    source_label: str
    reference: str | None
    provider_transaction_id: str | None
    amount: Decimal
    currency: str
    cashier: str | None
    payment_status: str
    notes: str | None
    allocations: list[AllocationRow]
    allocated_total: Decimal
    property_name: str


class ShiftRow(BaseModel):
    id: uuid.UUID
    cashier_id: uuid.UUID
    cashier: str | None
    business_date: date
    status: str
    opened_at: datetime
    closed_at: datetime | None
    opening_float: Decimal
    declared_cash: Decimal | None
    expected_cash: Decimal | None
    variance: Decimal | None
    cash_taken: Decimal
    payments_count: int
    #: Cash handed back out of this drawer. Subtracted from expected.
    cash_refunded: Decimal = Decimal("0")
    refunds_count: int = 0
    #: Everything taken during the shift, by every method, and what it was
    #: given back on.
    #:
    #: The columns above are a DRAWER COUNT -- cash only, because cash is the
    #: only thing in the till. They answer "does the money in the tray match
    #: the book", and they were the only thing this row reported, so the other
    #: question a shift is asked -- how much business went through this desk
    #: while this person was on it -- had no answer at all. A cashier who took
    #: ninety thousand on cards and four thousand in cash showed as a
    #: four-thousand shift.
    #:
    #: Kept as separate fields rather than folded into cash_taken: netting
    #: card into a drawer figure is how a till starts expecting money that was
    #: never in it.
    total_taken: Decimal = Decimal("0")
    total_refunded: Decimal = Decimal("0")
    #: Cash banked mid-shift. Subtracted from expected, like a refund, but it
    #: is not one: the money moved from the tray to the safe and is still the
    #: property's. Kept as its own figure so "where did the 60,000 go" has an
    #: answer that a quietly reduced float could not give.
    cash_dropped: Decimal = Decimal("0")
    drops_count: int = 0
    #: Per method, biggest first, for the breakdown under the total.
    by_method: list[dict] = []
    notes: str | None


class Shifts(BaseModel):
    shifts: list[ShiftRow]
    my_open_shift: ShiftRow | None
    can_open: bool


class OpenShiftIn(BaseModel):
    opening_float: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = Field(default=None, max_length=600)


class CloseShiftIn(BaseModel):
    declared_cash: Decimal = Field(ge=0)
    notes: str | None = Field(default=None, max_length=600)


class OpenFolio(BaseModel):
    folio_id: uuid.UUID
    guest_name: str | None
    reservation_number: str | None
    room_code: str | None
    balance: Decimal
    currency: str


#: Where a bag of cash can go. Short list on purpose: a free-text destination
#: is a field nobody can report on.
DROP_DESTINATIONS = ("safe", "bank", "head_office")
DROP_DESTINATION_LABELS = {
    "safe": "Property safe", "bank": "Bank deposit",
    "head_office": "Head office",
}


class CashDropIn(BaseModel):
    amount: Decimal = Field(gt=0)
    destination: str = "safe"
    #: The deposit slip or bag number. What somebody tracing this money later
    #: actually has to go on.
    reference: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=300)
    witnessed_by: uuid.UUID | None = None


class CashDropRow(BaseModel):
    id: uuid.UUID
    amount: Decimal
    currency: str
    destination: str
    destination_label: str
    reference: str | None
    note: str | None
    dropped_at: datetime
    dropped_by_name: str | None
    witnessed_by_name: str | None


class CollectIn(BaseModel):
    folio_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    method: str
    reference: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=300)


class Collected(BaseModel):
    payment_id: uuid.UUID
    amount: Decimal
    method_label: str
    balance_after: Decimal


class Options(BaseModel):
    methods: list[dict]
    sources: list[dict]
    #: Rooms that money has actually gone through at this property, so the
    #: filter offers a room only when picking it would return something.
    #: Listing the whole rack would put 300 rooms in a dropdown, most of them
    #: dead ends.
    rooms: list[dict]
    can_collect: bool
    can_manage_shift: bool
    can_export: bool


# --------------------------------------------------------------------------
# Shared reads
# --------------------------------------------------------------------------
def _property(db: Session, property_id: uuid.UUID):
    row = db.execute(
        text("SELECT id, organization_id, name, currency "
             "FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return row


def _may(db: Session, caller: Caller, property_id: uuid.UUID, action: str) -> bool:
    """Whether the caller holds a payments action, for greying out buttons."""
    return db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "payments", "act": action,
         "prop": property_id},
    ).first() is not None


def _today(db: Session, property_id: uuid.UUID) -> date:
    """The property's open business day, or the calendar date if none is open.

    Night audit owns ``business_days``; until it has run for a property there is
    no row, and the calendar date is the honest fallback rather than a guess.
    """
    row = db.execute(
        # The OLDEST unclosed day, which is the one the property is
        # actually trading: you cannot be selling the 13th while the
        # 12th is still open, and the night audit closes them oldest
        # first. Taking the newest instead stamped money with a day the
        # audit had not reached, so a cash payment landed on a date the
        # drawer was never counted against.
        text("SELECT business_date FROM finance.business_days "
             "WHERE property_id = :p AND status = 'open' "
             "ORDER BY business_date ASC LIMIT 1"),
        {"p": property_id},
    ).scalar()
    return row or db.execute(text("SELECT CURRENT_DATE")).scalar_one()


def _reference(row) -> str | None:
    """What the guest can quote back to find this payment.

    Falls back to the gateway's transaction id. An online payment has no
    reference a person typed in, but it does have the id the gateway knows it
    by -- which is the string that finds it in their dashboard, and the one a
    guest will be reading off their bank statement. Showing a dash while
    holding that id helps nobody.
    """
    return row["reference"] or row["provider_transaction_id"] or None


def _cashier(row) -> str | None:
    """Who took the money, where anyone did.

    An unattended source has no cashier and never will. Naming the channel
    instead of leaving it blank distinguishes "nobody was involved" from
    "somebody did not record themselves", which look identical as a dash.
    """
    if row["cashier"]:
        return row["cashier"]
    if row["source"] in UNATTENDED_SOURCES:
        return SOURCE_LABELS.get(row["source"], "Online")
    return None


# One payment, with everything a person needs to recognise it: who paid, which
# booking, which room. The folio is the join — a payment reaches a guest only
# through the folio it was allocated to.
_TXN_SQL = """
    SELECT p.id AS payment_id, p.received_at, bd.business_date,
           lower(p.method) AS method,
           p.reference, p.amount,
           p.currency, p.status AS payment_status, p.source, p.notes,
           p.provider_transaction_id,
           'payment' AS kind, p.cashier_shift_id,
           -- The person who took it, named however it can be. `cashier_id`
           -- is set only where the caller recorded themselves on the payment
           -- and is null on most rows, so a payment sitting in a named
           -- cashier's drawer still showed a dash in the Cashier column --
           -- most visibly when the list has just been filtered TO that
           -- cashier's shift. The drawer knows whose it is; the refund arm
           -- below has always read the name that way.
           COALESCE(u.display_name, su.display_name) AS cashier,
           fa.folio_id, r.id AS reservation_id, r.number AS reservation_number,
           g.full_name AS guest_name,
           rm.code AS room_code
    FROM finance.payments p
    -- The day the LEDGER put this money on, which is not the day the clock
    -- says. A payment taken at 09:51 on the 19th while the property is still
    -- trading the 18th belongs to the 18th, and the drawer counting the 18th
    -- has to be able to see it.
    LEFT JOIN LATERAL (
        SELECT e.business_date FROM finance.folio_entries e
         WHERE e.source_id = p.id::text AND e.entry_type = 'credit'
         ORDER BY e.posted_at LIMIT 1
    ) bd ON TRUE
    LEFT JOIN iam.users u ON u.id = p.cashier_id
    LEFT JOIN finance.cashier_shifts ps ON ps.id = p.cashier_shift_id
    LEFT JOIN iam.users su ON su.id = ps.cashier_id
    LEFT JOIN LATERAL (
        -- A payment can in principle span folios; the first allocation is the
        -- one that names the guest, and the detail view shows them all.
        SELECT pa.folio_id
        FROM finance.payment_allocations pa
        WHERE pa.payment_id = p.id
        ORDER BY pa.created_at
        LIMIT 1
    ) fa ON TRUE
    LEFT JOIN finance.folios f ON f.id = fa.folio_id
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
    WHERE p.property_id = :prop
"""

# The same shape, for money going the other way.
#
# A cashier who gave four refunds during a shift could see "−₹2,400 · 4
# refunds" on their shift row and nowhere at all which four. The refunds were
# recorded — in `finance.refunds`, as debits on each folio, and on the
# reversal request — but the one screen a cashier reconciles against listed
# payments only, so the day's takings and the day's givings-back lived on
# different pages.
#
# The amount is negated here rather than in the UI: a list where some rows
# mean "in" and others mean "out" has to say so in the number, or the column
# does not add up to what is in the drawer.
#
# `payment_id` carries the payment being refunded, not the refund, so clicking
# the row opens the payment it belongs to — which is where the reversal, its
# approval and its timeline already live.
_REFUND_TXN_SQL = """
    SELECT p.id AS payment_id, rf.created_at AS received_at, bd.business_date,
           lower(rf.method) AS method,
           coalesce(rf.provider_refund_id, p.reference) AS reference,
           -rf.amount AS amount,
           rf.currency, 'refunded' AS payment_status, p.source, rf.reason AS notes,
           rf.provider_refund_id,
           'refund' AS kind, rf.cashier_shift_id,
           u.display_name AS cashier,
           fa.folio_id, r.id AS reservation_id, r.number AS reservation_number,
           g.full_name AS guest_name,
           rm.code AS room_code
    FROM finance.refunds rf
    JOIN finance.payments p ON p.id = rf.payment_id
    LEFT JOIN LATERAL (
        SELECT e.business_date FROM finance.folio_entries e
         WHERE e.source_id = rf.id::text AND e.entry_type = 'debit'
         ORDER BY e.posted_at LIMIT 1
    ) bd ON TRUE
    -- Whoever had the drawer open, since a refund carries no cashier of its
    -- own. For a card refund there is no drawer and so no name, which is
    -- honest: nobody handed anything over.
    LEFT JOIN finance.cashier_shifts cs ON cs.id = rf.cashier_shift_id
    LEFT JOIN iam.users u ON u.id = cs.cashier_id
    LEFT JOIN LATERAL (
        SELECT pa.folio_id
        FROM finance.payment_allocations pa
        WHERE pa.payment_id = p.id
        ORDER BY pa.created_at
        LIMIT 1
    ) fa ON TRUE
    LEFT JOIN finance.folios f ON f.id = fa.folio_id
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
    WHERE rf.property_id = :prop AND rf.status = 'succeeded'
"""

#: Payments and refunds as one stream. Wrapped so the filters and the ORDER BY
#: are written once and cannot drift between the two arms.
_MOVEMENTS_SQL = f"""
    SELECT * FROM ({_TXN_SQL} UNION ALL {_REFUND_TXN_SQL}) m
"""


# --------------------------------------------------------------------------
# The numbers along the top
# --------------------------------------------------------------------------
@cashiering_router.get("/summary", response_model=Summary)
def summary(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """What came in today, how it was paid, and what is still owed."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    day = on_date or _today(db, property_id)

    rows = db.execute(
        text(
            """
            SELECT lower(p.method) AS method, sum(p.amount) AS amount,
                   count(*) AS n
            -- Same business-date rule as the transactions list below it.
            -- These two numbers sit on one screen and have to be the same
            -- population, or the total disagrees with the rows making it up.
            FROM finance.payments p
            LEFT JOIN LATERAL (
                SELECT e.business_date FROM finance.folio_entries e
                 WHERE e.source_id = p.id::text AND e.entry_type = 'credit'
                 ORDER BY e.posted_at LIMIT 1
            ) bd ON TRUE
            WHERE p.property_id = :prop
              AND p.status = 'succeeded'
              AND COALESCE(bd.business_date, CAST(p.received_at AS date)) = CAST(:d AS date)
            GROUP BY lower(p.method)
            """
        ),
        {"prop": property_id, "d": day},
    ).mappings().all()
    collected = sum((r["amount"] for r in rows), Decimal("0"))
    count = sum(r["n"] for r in rows)

    previous = db.execute(
        text(
            """
            SELECT COALESCE(sum(p.amount), 0)
            -- The day before, on the same business-date rule.
            FROM finance.payments p
            LEFT JOIN LATERAL (
                SELECT e.business_date FROM finance.folio_entries e
                 WHERE e.source_id = p.id::text AND e.entry_type = 'credit'
                 ORDER BY e.posted_at LIMIT 1
            ) bd ON TRUE
            WHERE p.property_id = :prop
              AND p.status = 'succeeded'
              AND COALESCE(bd.business_date, CAST(p.received_at AS date)) = CAST(:d AS date) - 1
            """
        ),
        {"prop": property_id, "d": day},
    ).scalar_one()

    # What is still owed AS OF THE DAY BEING TRADED: open folios whose debits
    # exceed their credits, counting only entries that have actually happened
    # by then. Read from the entries rather than a stored balance, so it
    # cannot drift.
    #
    # The business_date bound is the whole point. Without it this summed every
    # entry on every open folio regardless of when it falls, so a stay booked
    # for December and cancelled carried its cancellation fee into today's
    # figure -- money not yet owed, on a screen whose every other number is
    # scoped to one business day. It read 1,17,200 outstanding while the
    # High Balance Guest report, which does bound by date, correctly reported
    # nothing owed at all. Two screens, two answers, and the undated one was
    # the one on the wall.
    owed = db.execute(
        text(
            """
            SELECT COALESCE(sum(balance), 0) AS total, count(*) AS n
            FROM (
                SELECT f.id,
                       sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                                ELSE -e.amount END) AS balance
                FROM finance.folios f
                JOIN finance.folio_entries e ON e.folio_id = f.id
                WHERE f.property_id = :prop
                  AND f.status = 'open'
                  AND e.business_date <= :day
                GROUP BY f.id
                HAVING sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                                ELSE -e.amount END) > 0
            ) open_balances
            """
        ),
        {"prop": property_id, "day": day},
    ).mappings().first()

    change = None
    if previous > 0:
        change = int(round(100 * (collected - previous) / previous))
    elif collected > 0:
        change = 100

    slices = [
        MethodSlice(
            method=r["method"],
            label=METHOD_LABELS.get(r["method"], r["method"].replace("_", " ").title()),
            amount=r["amount"], count=r["n"],
            percent=int(round(100 * r["amount"] / collected)) if collected else 0,
        )
        for r in sorted(rows, key=lambda x: -x["amount"])
    ]
    return Summary(
        business_date=day, currency=prop["currency"] or "INR",
        collected=collected, collected_count=count,
        previous_day=previous, change_percent=change, methods=slices,
        outstanding=owed["total"], outstanding_folios=owed["n"],
    )


@cashiering_router.get("/options", response_model=Options)
def options(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """The vocabularies the screen offers, and what this caller may do."""
    assert_property_in_org(db, caller, property_id)
    return Options(
        methods=[{"value": m, "label": METHOD_LABELS[m],
                  "needs_reference": m in NEEDS_REFERENCE} for m in METHODS],
        sources=[{"value": s, "label": SOURCE_LABELS[s]} for s in SOURCES],
        rooms=[{"value": r, "label": r} for r in db.execute(
            text("""
                SELECT DISTINCT rm.code
                  FROM finance.payments p
                  JOIN finance.payment_allocations pa ON pa.payment_id = p.id
                  JOIN finance.folios f ON f.id = pa.folio_id
                  JOIN booking.reservation_units ru
                    ON ru.reservation_id = f.reservation_id
                  JOIN property.rooms rm ON rm.id = ru.assigned_room_id
                 WHERE p.property_id = :p AND rm.code IS NOT NULL
                 ORDER BY rm.code
            """),
            {"p": property_id},
        ).scalars()],
        can_collect=_may(db, caller, property_id, "create"),
        can_manage_shift=_may(db, caller, property_id, "edit"),
        can_export=_may(db, caller, property_id, "export"),
    )


# --------------------------------------------------------------------------
# The transaction list
# --------------------------------------------------------------------------
@cashiering_router.get("/transactions", response_model=list[Txn])
def transactions(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    method: str | None = Query(None),
    source: str | None = Query(None),
    shift_id: uuid.UUID | None = Query(None),
    #: One room, exactly. The free-text `q` already reaches room_code, but it
    #: reaches guest name, booking number and reference too -- so "201" also
    #: matches a card reference ending 201 and a booking numbered ...201. For
    #: "what went through room 201" that is a guess dressed as an answer.
    room: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Every movement of money on this date, newest first.

    Payments and refunds together. They are the same question — what went
    through this desk today — and a cashier counting a drawer needs both sides
    of it in one list.
    """
    assert_property_in_org(db, caller, property_id)
    day = on_date or _today(db, property_id)
    rows = db.execute(
        text(
            _MOVEMENTS_SQL
            + """
            -- Matched on the business date, not the wall clock.
            --
            -- `day` above is the business date, so filtering received_at
            -- against it compared two different things: a payment stamped to
            -- the 18th but taken at 09:51 on the 19th fell outside the 18th's
            -- window and vanished from the list -- while still being counted
            -- in the drawer the same screen was showing. The banner read
            -- "2,000 cash taken in 1 payment" above a table that did not
            -- contain it.
            --
            -- COALESCE because a payment with no folio entry has no business
            -- date of its own; its own clock is the only thing left to go on.
            -- A named shift defines its own scope and the day steps aside.
            -- A drawer opened at 00:20 and closed at 00:33 carries the
            -- PREVIOUS business date, and a night shift spans two of them
            -- outright; asking for one shift's takings and getting only the
            -- part that fell on one chosen day is never what the question
            -- meant. The shift link, not a time window, is what makes a
            -- payment this shift's: two cashiers can have drawers open at
            -- once, and a window would hand one of them the other's money.
            WHERE (CAST(:sh AS uuid) IS NOT NULL
                   OR COALESCE(m.business_date, CAST(m.received_at AS date))
                      = CAST(:d AS date))
              AND (CAST(:m AS varchar) IS NULL
                   OR m.method = lower(CAST(:m AS varchar)))
              AND (CAST(:s AS varchar) IS NULL OR m.source = CAST(:s AS varchar))
              AND (CAST(:sh AS uuid) IS NULL
                   OR m.cashier_shift_id = CAST(:sh AS uuid))
              AND (CAST(:room AS text) IS NULL
                   OR upper(m.room_code) = upper(CAST(:room AS text)))
              AND (CAST(:q AS text) IS NULL
                   OR m.guest_name ILIKE '%' || CAST(:q AS text) || '%'
                   OR m.reservation_number ILIKE '%' || CAST(:q AS text) || '%'
                   OR m.reference ILIKE '%' || CAST(:q AS text) || '%'
                   OR m.room_code ILIKE '%' || CAST(:q AS text) || '%')
            ORDER BY m.received_at DESC
            LIMIT :lim
            """
        ),
        {"prop": property_id, "d": day, "m": method, "s": source,
         "sh": shift_id, "room": (room or "").strip() or None,
         "q": q or None, "lim": limit},
    ).mappings().all()
    return [
        Txn(
            payment_id=r["payment_id"], received_at=r["received_at"],
            guest_name=r["guest_name"], folio_id=r["folio_id"],
            reservation_number=r["reservation_number"], room_code=r["room_code"],
            source=r["source"],
            source_label=("Refund" if r["kind"] == "refund"
                          else SOURCE_LABELS.get(r["source"], r["source"])),
            method=r["method"],
            method_label=METHOD_LABELS.get(r["method"], r["method"]),
            reference=_reference(r), amount=r["amount"], currency=r["currency"],
            cashier=_cashier(r), payment_status=r["payment_status"],
            kind=r["kind"],
        )
        for r in rows
    ]


@cashiering_router.get("/payments/{payment_id}", response_model=PaymentDetail)
def payment_detail(
    payment_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """One payment, and what it actually settled.

    The allocation breakdown is not stored anywhere: it is worked out by asking
    what the folio owed at the moment the payment landed. A payment credits the
    folio as a whole, so attributing it to categories means apportioning it
    across the debits it covered — which is what a guest asking "what was this
    ₹12,560 for" is really asking.
    """
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    row = db.execute(
        text(_TXN_SQL + " AND p.id = :pid"),
        {"prop": property_id, "pid": payment_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    allocated = db.execute(
        text("SELECT COALESCE(sum(amount), 0) FROM finance.payment_allocations "
             "WHERE payment_id = :pid"),
        {"pid": payment_id},
    ).scalar_one()

    breakdown: list[AllocationRow] = []
    if row["folio_id"] is not None:
        charges = db.execute(
            text(
                """
                SELECT COALESCE(cc.revenue_category, e.source_type, 'misc')
                           AS category,
                       sum(e.amount) AS amount
                FROM finance.folio_entries e
                LEFT JOIN finance.charge_codes cc ON cc.id = e.charge_code_id
                WHERE e.folio_id = :f
                  AND e.entry_type = 'debit'
                  AND e.posted_at <= :at
                  AND e.reversal_of_id IS NULL
                GROUP BY 1
                ORDER BY 2 DESC
                """
            ),
            {"f": row["folio_id"], "at": row["received_at"]},
        ).mappings().all()
        charged = sum((c["amount"] for c in charges), Decimal("0"))
        if charged > 0:
            # Apportion the payment across what it covered. The last row takes
            # the rounding so the parts always add back to the whole.
            running = Decimal("0")
            for i, c in enumerate(charges):
                if i == len(charges) - 1:
                    part = allocated - running
                else:
                    part = (allocated * c["amount"] / charged).quantize(Decimal("0.01"))
                    running += part
                if part <= 0:
                    continue
                key = (c["category"] or "misc").lower()
                breakdown.append(AllocationRow(
                    category=CATEGORY_LABELS.get(key, key.replace("_", " ").title()),
                    description=f"Posted to folio before {row['received_at']:%d %b %H:%M}",
                    amount=part,
                ))
    if not breakdown and allocated > 0:
        # A payment taken before anything was charged — a deposit, usually.
        breakdown.append(AllocationRow(
            category="On account",
            description="Held against the folio; nothing was charged yet",
            amount=allocated,
        ))

    return PaymentDetail(
        payment_id=row["payment_id"], received_at=row["received_at"],
        guest_name=row["guest_name"], folio_id=row["folio_id"],
        reservation_id=row["reservation_id"],
        reservation_number=row["reservation_number"], room_code=row["room_code"],
        method=row["method"],
        method_label=METHOD_LABELS.get(row["method"], row["method"]),
        source_label=SOURCE_LABELS.get(row["source"], row["source"]),
        reference=_reference(row),
        provider_transaction_id=row["provider_transaction_id"],
        amount=row["amount"], currency=row["currency"],
        cashier=_cashier(row),
        payment_status=row["payment_status"], notes=row["notes"],
        allocations=breakdown, allocated_total=allocated,
        property_name=prop["name"],
    )


# --------------------------------------------------------------------------
# Shifts
# --------------------------------------------------------------------------
_SHIFT_SQL = """
    SELECT s.*, u.display_name AS cashier,
           COALESCE(cash.total, 0) AS cash_taken,
           COALESCE(cash.n, 0) AS payments_count,
           COALESCE(refunded.total, 0) AS cash_refunded,
           COALESCE(refunded.n, 0) AS refunds_count,
           COALESCE(alltake.total, 0) AS total_taken,
           COALESCE(allback.total, 0) AS total_refunded,
           COALESCE(drops.total, 0) AS cash_dropped,
           COALESCE(drops.n, 0) AS drops_count
    FROM finance.cashier_shifts s
    LEFT JOIN iam.users u ON u.id = s.cashier_id
    LEFT JOIN LATERAL (
        SELECT sum(p.amount) AS total, count(*) AS n
        FROM finance.payments p
        WHERE p.cashier_shift_id = s.id
          AND p.status = 'succeeded'
          AND lower(p.method) = 'cash'
    ) cash ON TRUE
    -- Cash handed back comes out of the same drawer it went into, so the
    -- count has to know about it. Without this a cashier who refunded a guest
    -- declared less than expected and showed a shortage they did not cause.
    LEFT JOIN LATERAL (
        SELECT coalesce(sum(rf.amount), 0) AS total, count(*) AS n
        FROM finance.refunds rf
        WHERE rf.cashier_shift_id = s.id
          AND rf.status = 'succeeded'
          AND lower(rf.method) = 'cash'
    ) refunded ON TRUE
    -- Cash lifted out of the tray mid-shift and put in the safe. It reduces
    -- what the drawer should hold without being a refund: the money has not
    -- left the property, only the till.
    LEFT JOIN LATERAL (
        SELECT COALESCE(sum(d.amount), 0) AS total, count(*) AS n
        FROM finance.cash_drops d
        WHERE d.cashier_shift_id = s.id
    ) drops ON TRUE
    -- The same two sums without the method filter: the shift's takings rather
    -- than its drawer.
    LEFT JOIN LATERAL (
        SELECT sum(p.amount) AS total
        FROM finance.payments p
        WHERE p.cashier_shift_id = s.id AND p.status = 'succeeded'
    ) alltake ON TRUE
    LEFT JOIN LATERAL (
        SELECT sum(rf.amount) AS total
        FROM finance.refunds rf
        WHERE rf.cashier_shift_id = s.id AND rf.status = 'succeeded'
    ) allback ON TRUE
    WHERE s.property_id = :prop
"""


def _by_method(db: Session, shift_id) -> list[dict]:
    """What this shift took, split by how it was paid, biggest first."""
    return [
        {"method": m["method"],
         "label": METHOD_LABELS.get(m["method"], m["method"]),
         "amount": str(m["amount"]), "count": m["n"]}
        for m in db.execute(
            text("""
                SELECT lower(p.method) AS method, sum(p.amount) AS amount,
                       count(*) AS n
                  FROM finance.payments p
                 WHERE p.cashier_shift_id = :s AND p.status = 'succeeded'
                 GROUP BY lower(p.method)
                 ORDER BY sum(p.amount) DESC
            """),
            {"s": shift_id},
        ).mappings()
    ]


def _shift_row(r, db: Session | None = None) -> ShiftRow:
    return ShiftRow(
        id=r["id"], cashier_id=r["cashier_id"], cashier=r["cashier"],
        business_date=r["business_date"], status=r["status"],
        opened_at=r["opened_at"], closed_at=r["closed_at"],
        opening_float=r["opening_float"], declared_cash=r["declared_cash"],
        expected_cash=r["expected_cash"], variance=r["variance"],
        cash_taken=r["cash_taken"], payments_count=r["payments_count"],
        cash_refunded=r["cash_refunded"], refunds_count=r["refunds_count"],
        total_taken=r["total_taken"], total_refunded=r["total_refunded"],
        cash_dropped=r["cash_dropped"], drops_count=r["drops_count"],
        by_method=_by_method(db, r["id"]) if db is not None else [],
        notes=r["notes"],
    )


@cashiering_router.get("/shifts", response_model=Shifts)
def list_shifts(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Today's shifts, plus this cashier's own open drawer if they have one."""
    assert_property_in_org(db, caller, property_id)
    day = on_date or _today(db, property_id)
    rows = db.execute(
        text(_SHIFT_SQL + " AND (s.business_date = :d OR s.status = 'open') "
                          " ORDER BY s.opened_at DESC"),
        {"prop": property_id, "d": day},
    ).mappings().all()
    shifts = [_shift_row(r, db) for r in rows]
    mine = next((s for s in shifts
                 if s.status == "open" and s.cashier_id == caller.user_id), None)
    return Shifts(shifts=shifts, my_open_shift=mine,
                  can_open=_may(db, caller, property_id, "edit"))


@cashiering_router.post("/shifts", response_model=ShiftRow, status_code=201)
def open_shift(
    property_id: uuid.UUID,
    body: OpenShiftIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Open a drawer, with the float that went into it."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    day = _today(db, property_id)

    already = db.execute(
        text("SELECT id, opened_at FROM finance.cashier_shifts "
             "WHERE cashier_id = :u AND status = 'open'"),
        {"u": caller.user_id},
    ).mappings().first()
    if already is not None:
        raise HTTPException(
            status_code=409,
            detail=f"You already have a shift open since "
                   f"{already['opened_at']:%d %b, %H:%M}. Close it before "
                   f"opening another.",
        )

    shift_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.cashier_shifts
                (id, organization_id, property_id, cashier_id, business_date,
                 status, opening_float, notes, opened_by)
            VALUES (:id, :org, :prop, :u, :d, 'open', :float, :notes, :u)
            """
        ),
        {"id": shift_id, "org": prop["organization_id"], "prop": property_id,
         "u": caller.user_id, "d": day, "float": body.opening_float,
         "notes": body.notes},
    )
    record_audit(
        db, action="cashier_shift.opened", entity_type="cashier_shift",
        entity_id=str(shift_id), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"opening_float": str(body.opening_float),
               "business_date": str(day)},
    )
    row = db.execute(text(_SHIFT_SQL + " AND s.id = :id"),
                     {"prop": property_id, "id": shift_id}).mappings().first()
    return _shift_row(row, db)


@cashiering_router.post("/shifts/{shift_id}/close", response_model=ShiftRow)
def close_shift(
    shift_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CloseShiftIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Count the drawer and close it.

    Expected cash is the float, plus every cash payment taken on this shift,
    less every cash refund paid out of it. Anything else — card, UPI — never
    entered the drawer, so it plays no part in the count either way.
    """
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    row = db.execute(text(_SHIFT_SQL + " AND s.id = :id"),
                     {"prop": property_id, "id": shift_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    if row["status"] != "open":
        raise HTTPException(status_code=409, detail="That shift is already closed.")
    # Closing somebody else's drawer is a supervisor action, not a routine one.
    if row["cashier_id"] != caller.user_id and not _may(db, caller, property_id,
                                                        "approve"):
        raise HTTPException(
            status_code=403,
            detail=f"This drawer belongs to {row['cashier'] or 'another cashier'}. "
                   f"Closing it needs the payments approve permission.",
        )

    # In, minus out. A drawer holds what was put in it, plus what it took,
    # less what it handed back and less what was banked out of it mid-shift.
    #
    # Without the drops term a cashier who followed procedure -- lifting most
    # of the tray into the safe at the busy hour -- closed on a shortage of
    # exactly the amount they had safeguarded.
    expected = (row["opening_float"] + row["cash_taken"]
                - row["cash_refunded"] - row["cash_dropped"])
    variance = body.declared_cash - expected
    db.execute(
        text(
            """
            UPDATE finance.cashier_shifts
               SET status = 'closed', closed_at = now(), closed_by = :u,
                   declared_cash = :dec, expected_cash = :exp, variance = :var,
                   notes = COALESCE(CAST(:n AS varchar), notes),
                   updated_at = now(), version = version + 1
             WHERE id = :id
            """
        ),
        {"id": shift_id, "u": caller.user_id, "dec": body.declared_cash,
         "exp": expected, "var": variance, "n": body.notes},
    )
    record_audit(
        db, action="cashier_shift.closed", entity_type="cashier_shift",
        entity_id=str(shift_id), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject, reason=body.notes,
        after={"expected": str(expected), "declared": str(body.declared_cash),
               "variance": str(variance), "cashier": row["cashier"]},
    )
    out = db.execute(text(_SHIFT_SQL + " AND s.id = :id"),
                     {"prop": property_id, "id": shift_id}).mappings().first()
    return _shift_row(out, db)


# --------------------------------------------------------------------------
# Collecting
# --------------------------------------------------------------------------
@cashiering_router.get("/open-folios", response_model=list[OpenFolio])
def open_folios(
    property_id: uuid.UUID,
    q: str | None = Query(None),
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Folios with something still owed, for the collect dialog.

    Bounded by the day the property is trading, like the Outstanding figure on
    the header. A folio whose only debit falls in December is not owing
    anything a cashier can collect in September, and offering it in the collect
    dialog invites a payment against a charge that has not been made.
    """
    assert_property_in_org(db, caller, property_id)
    day = _today(db, property_id)
    rows = db.execute(
        text(
            """
            SELECT f.id AS folio_id, f.currency,
                   sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                            ELSE -e.amount END) AS balance,
                   max(r.number) AS reservation_number,
                   max(g.full_name) AS guest_name,
                   max(rm.code) AS room_code
            FROM finance.folios f
            JOIN finance.folio_entries e ON e.folio_id = f.id
            LEFT JOIN booking.reservations r ON r.id = f.reservation_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            LEFT JOIN booking.reservation_units ru ON ru.reservation_id = r.id
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            WHERE f.property_id = :prop
              AND f.status = 'open'
              AND e.business_date <= :day
              AND (CAST(:q AS text) IS NULL
                   OR g.full_name ILIKE '%' || CAST(:q AS text) || '%'
                   OR r.number   ILIKE '%' || CAST(:q AS text) || '%'
                   OR rm.code    ILIKE '%' || CAST(:q AS text) || '%')
            GROUP BY f.id, f.currency
            HAVING sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                            ELSE -e.amount END) > 0
            ORDER BY 3 DESC
            LIMIT 100
            """
        ),
        {"prop": property_id, "q": q or None, "day": day},
    ).mappings().all()
    return [
        OpenFolio(
            folio_id=r["folio_id"], guest_name=r["guest_name"],
            reservation_number=r["reservation_number"], room_code=r["room_code"],
            balance=r["balance"], currency=r["currency"],
        )
        for r in rows
    ]


def _drops(db: Session, shift_id) -> list[CashDropRow]:
    return [
        CashDropRow(
            id=d["id"], amount=d["amount"], currency=d["currency"],
            destination=d["destination"],
            destination_label=DROP_DESTINATION_LABELS.get(
                d["destination"], d["destination"]),
            reference=d["reference"], note=d["note"],
            dropped_at=d["dropped_at"],
            dropped_by_name=d["dropped_by_name"],
            witnessed_by_name=d["witnessed_by_name"],
        )
        for d in db.execute(
            text("""
                SELECT d.id, d.amount, d.currency, d.destination, d.reference,
                       d.note, d.dropped_at,
                       u.display_name AS dropped_by_name,
                       w.display_name AS witnessed_by_name
                  FROM finance.cash_drops d
                  LEFT JOIN iam.users u ON u.id = d.dropped_by
                  LEFT JOIN iam.users w ON w.id = d.witnessed_by
                 WHERE d.cashier_shift_id = :s
                 ORDER BY d.dropped_at DESC
            """),
            {"s": shift_id},
        ).mappings()
    ]


@cashiering_router.get("/shifts/{shift_id}/drops",
                       response_model=list[CashDropRow])
def list_cash_drops(
    shift_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """What has been banked out of this drawer, newest first."""
    assert_property_in_org(db, caller, property_id)
    if db.execute(
        text("SELECT 1 FROM finance.cashier_shifts "
             "WHERE id = :id AND property_id = :p"),
        {"id": shift_id, "p": property_id},
    ).first() is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    return _drops(db, shift_id)


@cashiering_router.post("/shifts/{shift_id}/drops",
                        response_model=CashDropRow, status_code=201)
def record_cash_drop(
    shift_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CashDropIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Bank part of the drawer without closing it.

    The money moves from the tray to the safe. It is not a refund -- nothing
    went back to a guest -- so it does not touch a folio or the ledger; the
    only thing that changes is what this drawer should still hold.

    Refused on a closed shift. A drawer that has been counted and signed off
    cannot quietly have money removed from it afterwards: that is a correction
    to a closed count, which is a different act needing a different authority.
    """
    assert_property_in_org(db, caller, property_id)
    if body.destination not in DROP_DESTINATIONS:
        raise HTTPException(
            status_code=422,
            detail="Unknown destination. Expected one of: "
                   + ", ".join(DROP_DESTINATIONS) + ".")

    row = db.execute(text(_SHIFT_SQL + " AND s.id = :id"),
                     {"prop": property_id, "id": shift_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    if row["status"] != "open":
        raise HTTPException(
            status_code=409,
            detail="That drawer is closed. A drop can only be recorded while "
                   "it is open.")
    if row["cashier_id"] != caller.user_id and not _may(db, caller, property_id,
                                                        "approve"):
        raise HTTPException(
            status_code=403,
            detail=f"This drawer belongs to {row['cashier'] or 'another cashier'}. "
                   f"Banking out of it needs the payments approve permission.")

    # What is actually in the tray right now. Dropping more than that would
    # describe money the drawer does not hold.
    in_tray = (row["opening_float"] + row["cash_taken"]
               - row["cash_refunded"] - row["cash_dropped"])
    if body.amount > in_tray:
        raise HTTPException(
            status_code=422,
            detail=f"The drawer holds {in_tray:,.2f}. A drop cannot be more "
                   f"than that.")

    drop_id = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO finance.cash_drops
                (id, organization_id, property_id, cashier_shift_id, amount,
                 currency, destination, reference, note, dropped_by,
                 witnessed_by)
            VALUES (:id, :org, :prop, :shift, :amt, :cur, :dest, :ref, :note,
                    :by, :wit)
        """),
        {"id": drop_id, "org": row["organization_id"], "prop": property_id,
         "shift": shift_id, "amt": body.amount,
         "cur": _property(db, property_id)["currency"] or "INR",
         "dest": body.destination,
         "ref": (body.reference or "").strip() or None,
         "note": (body.note or "").strip() or None,
         "by": caller.user_id, "wit": body.witnessed_by},
    )
    record_audit(
        db, action="cash_drop.recorded", entity_type="cashier_shift",
        entity_id=str(shift_id), organization_id=row["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.note,
        after={"drop_id": str(drop_id), "amount": str(body.amount),
               "destination": body.destination,
               "reference": body.reference},
    )
    return next(d for d in _drops(db, shift_id) if d.id == drop_id)


@cashiering_router.post("/payments", response_model=Collected, status_code=201)
def collect(
    property_id: uuid.UUID,
    body: CollectIn,
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Take a payment against a folio.

    The money is captured by the same ledger call the deposit screen and
    checkout use — there is one place a payment is recorded, and this is not a
    second one. What this adds afterwards is the part the ledger has no opinion
    about: who took it, what reference the guest was given, and which drawer it
    belongs to.
    """
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    day = _today(db, property_id)

    if body.method not in METHODS:
        raise HTTPException(status_code=422, detail="Unknown payment method.")
    if body.method in NEEDS_REFERENCE and not (body.reference or "").strip():
        raise HTTPException(
            status_code=422,
            detail=f"A {METHOD_LABELS[body.method]} payment needs a reference — "
                   f"it is what the guest quotes back if the payment has to be "
                   f"traced.",
        )

    folio = db.execute(
        text("SELECT id, status, currency FROM finance.folios "
             "WHERE id = :f AND property_id = :prop"),
        {"f": body.folio_id, "prop": property_id},
    ).mappings().first()
    if folio is None:
        raise HTTPException(status_code=404, detail="Folio not found")
    if folio["status"] != "open":
        raise HTTPException(
            status_code=409,
            detail="That folio is closed. Payments can only be taken against an "
                   "open folio.",
        )

    # Cash goes into a drawer, so there has to be a drawer open to put it in.
    # This route used to carry that rule itself, and was the only one that
    # did: the folio's Add payment dialog and the deposit screen let the same
    # cash through untracked. The rule now lives in post_payment, where every
    # payment passes, so all three doors are guarded by one sentence and this
    # only has to find the drawer.
    shift_id = db.execute(
        text("SELECT id FROM finance.cashier_shifts "
             "WHERE cashier_id = :u AND property_id = :prop AND status = 'open'"),
        {"u": caller.user_id, "prop": property_id},
    ).scalar()

    try:
        res = post_payment(
            db,
            organization_id=prop["organization_id"],
            property_id=property_id,
            method=body.method,
            business_date=day,
            allocations=[Allocation(folio_id=body.folio_id, amount=body.amount)],
            currency=folio["currency"] or prop["currency"] or "INR",
            note=body.notes,
            posted_by=caller.subject,
            # Named up front now that post_payment takes it. It used to be
            # patched on by the UPDATE below, which is why the same cash taken
            # from a folio -- which never ran that UPDATE -- reached no drawer.
            cashier_shift_id=shift_id,
        )
    except LedgerError as exc:
        # 409, not 422, when the ledger says the state is wrong rather than
        # the request: "open a drawer first" is something the cashier can act
        # on, and a flat 422 told the screen it was a validation error.
        raise HTTPException(status_code=409 if exc.conflict else 422,
                            detail=str(exc)) from exc

    db.execute(
        text("UPDATE finance.payments SET cashier_id = :u, reference = :ref, "
             "notes = :n, source = 'front_desk' "
             "WHERE id = :id"),
        {"u": caller.user_id, "ref": (body.reference or "").strip() or None,
         "n": body.notes, "id": res.payment_id},
    )

    balance = db.execute(
        text("SELECT COALESCE(sum(CASE WHEN entry_type = 'debit' THEN amount "
             "ELSE -amount END), 0) FROM finance.folio_entries WHERE folio_id = :f"),
        {"f": body.folio_id},
    ).scalar_one()

    record_audit(
        db, action="payment.collected", entity_type="payment",
        entity_id=str(res.payment_id), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject, reason=body.notes,
        after={"folio_id": str(body.folio_id), "amount": str(body.amount),
               "method": body.method, "reference": body.reference,
               "balance_after": str(balance)},
    )
    return Collected(
        payment_id=res.payment_id, amount=body.amount,
        method_label=METHOD_LABELS[body.method], balance_after=balance,
    )
