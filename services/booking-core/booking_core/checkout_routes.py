"""Guest Check-Out API (screen 007).

The mirror of check-in, and the half that was missing: the engine to close a
stay has existed since the first migration, but nothing ever called it, so a
guest could be checked in and could pay and could never leave.

As with check-in, one transaction does the whole thing — take the final
payment, close the stay, free the room, record the handover — so a guest is
never half-departed. And as with check-in, this sequences work that already
exists rather than reimplementing it: ``flow.check_out`` releases the future
nights and the room calendar entry, and the payment is written the same shape
``post_payment`` writes.

Two judgements are worth stating.

**A balance does not block departure.** Guests leave owing money — a company is
invoiced, a charge is disputed, a deposit is still being counted. Refusing
check-out until the folio is zero would not stop that happening; it would just
stop it being recorded. So the balance is *reported*, carried into the
check-out row as ``balance_at_checkout``, and the screen makes the operator
confirm it deliberately.

**The folio total is captured at departure**, because a folio keeps moving
afterwards. Asking later what someone owed when they walked out is only
answerable if it was written down at the time.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from chirala_common import payment_methods
from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from chirala_common.folio_posting import resolve_currency
from chirala_common.property_time import trading_day
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .resdetail_routes import local_today
from .folio_money import folio_money
from .flow import FlowError, check_out
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

checkout_router = APIRouter(tags=["check-out"], route_class=TransactionalRoute)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class DepartureRow(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    number: str
    guest_name: str | None
    room: str | None
    room_id: uuid.UUID | None = None
    room_type: str
    arrival_date: date
    departure_date: date
    nights: int
    # Occupancy, so Arrivals, In-house and Departures can all show the same
    # columns. A desk reading three lists should not have to relearn each one.
    adults: int
    children: int
    unit_status: str
    #: Board or rate basis, whichever the booking carries.
    plan: str | None = None
    # The three figures a desk reads together. Balance alone says what is left
    # to collect but not whether that is a stay nobody has paid for or one
    # settled to the rupee, which is the difference between a quiet checkout
    # and an argument.
    # Identifiers the row-level actions need: charge and payment are
    # posted against a folio and scoped by organisation, and the
    # housekeeping actions address the room by id, not by its code.
    organization_id: uuid.UUID | None = None
    folio_id: uuid.UUID | None = None
    #: False where no folio has been opened at all.
    has_folio: bool = False
    total: Decimal
    paid: Decimal
    balance: Decimal
    checked_out: bool
    # 'Due Out Today' on the mockup, plus the case it does not cover: a guest
    # whose departure date has already passed and who is still in the room.
    due_label: str


class FolioLine(BaseModel):
    id: uuid.UUID
    business_date: date
    description: str
    department: str
    qty: int
    amount: Decimal
    entry_type: str


class CheckOutView(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    number: str
    guest_name: str | None
    room: str | None
    room_type: str
    arrival_date: date
    departure_date: date
    nights: int
    unit_status: str
    already_checked_out: bool
    due_label: str

    folio_id: uuid.UUID | None
    lines: list[FolioLine]
    subtotal: Decimal
    taxes: Decimal
    grand_total: Decimal
    advance_paid: Decimal
    balance_due: Decimal
    deposit_held: Decimal
    currency: str


class CompleteCheckOutIn(BaseModel):
    payment_amount: Decimal = Field(default=Decimal("0"), ge=0)
    payment_method: str | None = Field(default=None, max_length=30)
    reference: str | None = Field(default=None, max_length=120)
    refund_deposit: bool = False
    key_returned: bool = False
    housekeeping_notified: bool = False
    feedback_scheduled: bool = False
    # Required when a balance remains, so leaving one is a decision rather than
    # an oversight.
    allow_outstanding_balance: bool = False
    notes: str | None = Field(default=None, max_length=500)


class CompleteCheckOutOut(BaseModel):
    reservation_unit_id: uuid.UUID
    room: str | None
    collected: Decimal
    deposit_refunded: Decimal
    balance_at_checkout: Decimal
    nights_released: int
    warnings: list[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_UNIT_SQL = """
    SELECT ru.id AS unit_id, ru.reservation_id, ru.arrival_date,
           ru.departure_date, ru.status AS unit_status, ru.assigned_room_id,
           ru.adults, ru.children,
           ru.organization_id, ru.property_id,
           r.number, r.currency, r.status AS reservation_status,
           rt.name AS room_type,
           rm.code AS room,
           -- Board basis, so these lists can describe a room the way the
           -- bookings list already does. Meal plan names the board; the rate
           -- plan names the basis, and either is more use than neither.
           COALESCE(mp.name, rp.name) AS plan,
           g.full_name AS guest_name,
           (co.id IS NOT NULL) AS has_checkout,
           COALESCE(ci.deposit_amount, 0) AS deposit_held
    FROM booking.reservation_units ru
    JOIN booking.reservations r ON r.id = ru.reservation_id
    LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
    LEFT JOIN property.meal_plans mp ON mp.id = ru.meal_plan_id
    LEFT JOIN property.rate_plans rp ON rp.id = ru.rate_plan_id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    -- The room's own guest where a rooming list named one: checking out room
    -- 214 should show who slept in it, not the name on the group contract.
    LEFT JOIN engagement.guests g
           ON g.id = COALESCE(ru.guest_id, r.primary_guest_id)
    LEFT JOIN booking.stay_checkouts co ON co.reservation_unit_id = ru.id
    LEFT JOIN booking.stay_checkins ci ON ci.reservation_unit_id = ru.id
"""

# Department labels, so the folio reads like a bill rather than a table of
# source keys. Mirrors the finance side's own mapping.
_DEPARTMENTS = {
    "room_night": ("Room Charge", "Rooms"),
    "room_stay": ("Room Charge", "Rooms"),
    "restaurant": ("Restaurant", "Restaurant"),
    "minibar": ("Minibar", "Rooms"),
    "in_room_dining": ("In-room Dining", "Rooms"),
    "spa": ("Spa", "Spa"),
    "laundry": ("Laundry Service", "Laundry"),
    "transport": ("Transportation", "Transportation"),
    "banquet": ("Banquet", "Banquets"),
    "payment": ("Payment Received", "Payments"),
    "security_deposit": ("Security Deposit", "Payments"),
    "deposit_refund": ("Deposit Refund", "Payments"),
}


def _describe(source_type: str) -> tuple[str, str]:
    if source_type.endswith("_tax"):
        base = source_type[:-4]
        label, dept = _DEPARTMENTS.get(base, (base.replace("_", " ").title(), "Other"))
        return f"Taxes & Charges — {label}", dept
    return _DEPARTMENTS.get(
        source_type, (source_type.replace("_", " ").title(), "Other")
    )


def _due_label(row, today: date) -> str:
    if row["unit_status"] == "checked_out":
        return "Checked out"
    if row["departure_date"] == today:
        return "Due Out Today"
    if row["departure_date"] < today:
        overdue = (today - row["departure_date"]).days
        return f"Overdue by {overdue} day{'s' if overdue != 1 else ''}"
    days = (row["departure_date"] - today).days
    return f"Due out in {days} day{'s' if days != 1 else ''}"


def _folio(db: Session, reservation_id: uuid.UUID):
    """The folio and its lines, or None where none has been opened."""
    folio = db.execute(
        text("SELECT id, currency FROM finance.folios "
             "WHERE reservation_id = :r ORDER BY created_at LIMIT 1"),
        {"r": reservation_id},
    ).mappings().first()
    if folio is None:
        return None, [], {}
    rows = db.execute(
        text(
            """
            SELECT id, business_date, entry_type, amount, source_type
            FROM finance.folio_entries
            WHERE folio_id = :f
            ORDER BY business_date, posted_at, id
            """
        ),
        {"f": folio["id"]},
    ).mappings().all()
    lines = []
    for r in rows:
        desc, dept = _describe(r["source_type"])
        lines.append(
            FolioLine(
                id=r["id"], business_date=r["business_date"], description=desc,
                department=dept, qty=1, amount=Decimal(r["amount"]),
                entry_type=r["entry_type"],
            )
        )
    debits = sum((l.amount for l in lines if l.entry_type == "debit"), Decimal("0"))
    credits = sum((l.amount for l in lines if l.entry_type == "credit"), Decimal("0"))
    taxes = sum((l.amount for l in lines
                 if l.entry_type == "debit" and l.description.startswith("Taxes")),
                Decimal("0"))
    return folio, lines, {
        "subtotal": debits - taxes, "taxes": taxes, "grand_total": debits,
        "advance_paid": credits, "balance": debits - credits,
    }


def _unit(db: Session, unit_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text(_UNIT_SQL + " WHERE ru.id = :id AND ru.property_id = :prop"),
        {"id": unit_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation unit not found")
    return row


# --------------------------------------------------------------------------
# Departures
# --------------------------------------------------------------------------
class StateCounts(BaseModel):
    """How many bookings sit in each state, for the tab strip."""

    reservations: int
    arrivals: int
    in_house: int
    departures: int
    on_date: date


@checkout_router.get("/reservation-counts", response_model=StateCounts)
def reservation_counts(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """The four numbers above the tabs.

    A desk should be able to see its workload without clicking through four
    lists to find three of them empty. One query rather than four list calls,
    because the counts are wanted together and the rows are not wanted at all.

    Each count repeats the WHERE of the list it labels — if these drifted, a
    tab would promise a number the list then failed to show, which is worse
    than no number.
    """
    assert_property_in_org(db, caller, property_id)
    target = on_date or local_today(db, property_id)
    row = db.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM booking.reservations r
                WHERE r.property_id = :prop) AS reservations,

              count(*) FILTER (
                WHERE ru.status NOT IN ('cancelled', 'no_show')
                  AND r.status IN ('confirmed', 'held')
                  AND ru.arrival_date <= :d AND ru.departure_date > :d
                  AND ru.status <> 'checked_in'
              ) AS arrivals,

              count(*) FILTER (WHERE ru.status = 'checked_in') AS in_house,

              -- Mirrors the departures list, including a guest who left
              -- early: the tab must not promise a number the list cannot show.
              count(*) FILTER (
                WHERE (ru.status = 'checked_in' AND ru.departure_date <= :d)
                   OR (ru.status = 'checked_out'
                       AND (co.checked_out_at::date = :d
                            OR ru.departure_date = :d))
              ) AS departures

            FROM booking.reservation_units ru
            JOIN booking.reservations r ON r.id = ru.reservation_id
            LEFT JOIN booking.stay_checkouts co
                   ON co.reservation_unit_id = ru.id
            WHERE ru.property_id = :prop
            """
        ),
        {"prop": property_id, "d": target},
    ).mappings().first()

    return StateCounts(
        reservations=int(row["reservations"]), arrivals=int(row["arrivals"]),
        in_house=int(row["in_house"]), departures=int(row["departures"]),
        on_date=target,
    )


@checkout_router.get("/in-house", response_model=list[DepartureRow])
def in_house(
    property_id: uuid.UUID,
    search: str | None = Query(None),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Everyone currently in a room.

    Arrivals is who is coming, Departures is who is going; neither answers
    "who is in the building right now", which is the question a desk is asked
    all day — by housekeeping, by a caller asking for a guest, by a manager
    walking past. It is every unit in ``checked_in``, whatever its dates, so an
    overstay and an early arrival both appear where somebody would look.

    The row is the same shape as a departure so one list component serves both;
    ``due_label`` says how close each guest is to leaving.
    """
    assert_property_in_org(db, caller, property_id)
    target = local_today(db, property_id)
    rows = db.execute(
        text(
            _UNIT_SQL
            + """
            WHERE ru.property_id = :prop
              AND ru.status = 'checked_in'
              AND (CAST(:q AS text) IS NULL
                   OR r.number ILIKE '%' || CAST(:q AS text) || '%'
                   OR g.full_name ILIKE '%' || CAST(:q AS text) || '%'
                   OR rm.code ILIKE '%' || CAST(:q AS text) || '%')
            ORDER BY rm.code NULLS LAST, r.number
            LIMIT 200
            """
        ),
        {"prop": property_id, "q": search or None},
    ).mappings().all()

    out = []
    for r in rows:
        money = folio_money(db, r["reservation_id"])
        out.append(
            DepartureRow(
                reservation_unit_id=r["unit_id"],
                reservation_id=r["reservation_id"],
                number=r["number"], guest_name=r["guest_name"], room=r["room"],
                room_id=r["assigned_room_id"],
                organization_id=r["organization_id"],
                folio_id=money["folio_id"],
                room_type=r["room_type"] or "—", plan=r["plan"],
                arrival_date=r["arrival_date"],
                departure_date=r["departure_date"],
                nights=(r["departure_date"] - r["arrival_date"]).days,
                adults=r["adults"], children=r["children"],
                unit_status=r["unit_status"],
                has_folio=money["has_folio"],
                total=money["total"], paid=money["paid"],
                balance=money["balance"],
                checked_out=False,
                due_label=_due_label(r, target),
            )
        )
    return out


@checkout_router.get("/departures", response_model=list[DepartureRow])
def departures(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    include_checked_out: bool = Query(False),
    search: str | None = Query(None),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Who is leaving, and what they still owe.

    Guests whose departure date has already passed but who are still in house
    are included whatever date is asked for — an overstay is the front desk's
    problem today, not on the day it was supposed to end.
    """
    assert_property_in_org(db, caller, property_id)
    target = on_date or local_today(db, property_id)
    rows = db.execute(
        text(
            _UNIT_SQL
            + """
            WHERE ru.property_id = :prop
              AND ru.status IN ('checked_in', 'checked_out')
              -- Who is leaving on this date, which is not the same as whose
              -- booking says they should. A guest who leaves early is gone
              -- today whatever the booking says, and keying only on
              -- departure_date hid them from the list entirely -- not merely
              -- behind the "show checked out" filter, but from both branches
              -- of the test, so no toggle could bring them back.
              AND ((ru.status = 'checked_in' AND ru.departure_date <= :d)
                   OR (ru.status = 'checked_out'
                       AND (co.checked_out_at::date = :d
                            OR ru.departure_date = :d)))
              AND (:inc OR ru.status <> 'checked_out')
              AND (CAST(:q AS text) IS NULL
                   OR r.number ILIKE '%' || CAST(:q AS text) || '%'
                   OR g.full_name ILIKE '%' || CAST(:q AS text) || '%')
            ORDER BY ru.departure_date, r.number
            LIMIT 200
            """
        ),
        {"prop": property_id, "d": target, "inc": include_checked_out,
         "q": search or None},
    ).mappings().all()

    out = []
    for r in rows:
        money = folio_money(db, r["reservation_id"])
        out.append(
            DepartureRow(
                reservation_unit_id=r["unit_id"], reservation_id=r["reservation_id"],
                number=r["number"], guest_name=r["guest_name"], room=r["room"],
                room_id=r["assigned_room_id"],
                organization_id=r["organization_id"],
                folio_id=money["folio_id"],
                room_type=r["room_type"] or "—", plan=r["plan"],
                arrival_date=r["arrival_date"], departure_date=r["departure_date"],
                nights=(r["departure_date"] - r["arrival_date"]).days,
                adults=r["adults"], children=r["children"],
                unit_status=r["unit_status"],
                has_folio=money["has_folio"],
                total=money["total"], paid=money["paid"],
                balance=money["balance"],
                checked_out=r["unit_status"] == "checked_out",
                due_label=_due_label(r, target),
            )
        )
    return out


@checkout_router.get(
    "/reservation-units/{unit_id}/check-out-view", response_model=CheckOutView
)
def check_out_view(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Everything the checkout screen shows, in one call."""
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    folio, lines, totals = _folio(db, row["reservation_id"])
    today = local_today(db, property_id)
    return CheckOutView(
        reservation_unit_id=row["unit_id"], reservation_id=row["reservation_id"],
        number=row["number"], guest_name=row["guest_name"], room=row["room"],
        room_type=row["room_type"] or "—",
        arrival_date=row["arrival_date"], departure_date=row["departure_date"],
        nights=(row["departure_date"] - row["arrival_date"]).days,
        unit_status=row["unit_status"],
        already_checked_out=row["unit_status"] == "checked_out",
        due_label=_due_label(row, today),
        folio_id=folio["id"] if folio else None, lines=lines,
        subtotal=totals.get("subtotal", Decimal("0")),
        taxes=totals.get("taxes", Decimal("0")),
        grand_total=totals.get("grand_total", Decimal("0")),
        advance_paid=totals.get("advance_paid", Decimal("0")),
        balance_due=totals.get("balance", Decimal("0")),
        deposit_held=Decimal(row["deposit_held"] or 0),
        currency=row["currency"],
    )


# --------------------------------------------------------------------------
# Completing the check-out
# --------------------------------------------------------------------------
def _post_credit(db: Session, row, *, folio_id, amount: Decimal, method: str,
                 source_type: str):
    """Write a payment and its folio credit — the shape post_payment writes.

    Including the canonical spelling of the method: finance validates it at
    its own endpoint, but this path does not go through that endpoint, so
    whatever the screen sent used to land in the ledger verbatim.
    """
    method = payment_methods.normalise(method)
    if method not in payment_methods.METHODS:
        raise HTTPException(
            422, f"Unknown payment method {method!r}. Known methods: "
                 + ", ".join(payment_methods.METHODS))
    payment_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.payments
                (id, organization_id, property_id, method, amount, currency,
                 status, received_at)
            VALUES (:id, :org, :prop, :method, :amt, :cur, 'succeeded', now())
            """
        ),
        {"id": payment_id, "org": row["organization_id"],
         "prop": row["property_id"], "method": method, "amt": amount,
         "cur": row["currency"]},
    )
    entry_id = uuid.uuid4()
    # The ledger's open day, not CURRENT_DATE: after midnight and before the
    # audit, money taken at the desk still belongs to the day being traded,
    # and CURRENT_DATE is UTC besides.
    db.execute(
        text(
            """
            INSERT INTO finance.folio_entries
                (id, organization_id, property_id, folio_id, entry_type,
                 amount, currency, business_date, source_type, source_id,
                 source_line_key)
            VALUES (:id, :org, :prop, :folio, 'credit', :amt, :cur,
                    :bd, :st, :src, :slk)
            """
        ),
        {"id": entry_id, "org": row["organization_id"],
         "prop": row["property_id"], "folio": folio_id, "amt": amount,
         "cur": resolve_currency(db, stated=None, folio_ids=[folio_id]),
         "bd": trading_day(db, row["property_id"]),
         "st": source_type, "src": str(payment_id),
         "slk": f"{source_type}:{payment_id}"},
    )
    db.execute(
        text(
            """
            INSERT INTO finance.payment_allocations
                (organization_id, property_id, payment_id, folio_id, amount,
                 folio_entry_id)
            VALUES (:org, :prop, :pid, :folio, :amt, :eid)
            """
        ),
        {"org": row["organization_id"], "prop": row["property_id"],
         "pid": payment_id, "folio": folio_id, "amt": amount, "eid": entry_id},
    )
    return payment_id


@checkout_router.post(
    "/reservation-units/{unit_id}/complete-check-out",
    response_model=CompleteCheckOutOut,
)
def complete_check_out(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CompleteCheckOutIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Settle, close the stay, free the room and record the handover.

    All in one transaction: a guest is never half-departed, and a payment is
    never taken for a check-out that then fails.
    """
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    warnings: list[str] = []

    if row["unit_status"] == "checked_out":
        raise HTTPException(status_code=409,
                            detail="This guest is already checked out.")
    if row["unit_status"] != "checked_in":
        raise HTTPException(
            status_code=422,
            detail=f"A unit in status '{row['unit_status']}' cannot be checked "
                   f"out. Only a guest who is in house can leave.",
        )

    folio, _, totals = _folio(db, row["reservation_id"])
    balance = totals.get("balance", Decimal("0"))

    collected = Decimal("0")
    payment_id = None
    if body.payment_amount > 0:
        if not body.payment_method:
            raise HTTPException(status_code=422,
                                detail="Choose how the payment was taken.")
        if folio is None:
            raise HTTPException(
                status_code=422,
                detail="This reservation has no folio, so a payment cannot be "
                       "posted against it.",
            )
        payment_id = _post_credit(db, row, folio_id=folio["id"],
                                  amount=body.payment_amount,
                                  method=body.payment_method,
                                  source_type="payment")
        collected = body.payment_amount
        balance -= collected

    refunded = Decimal("0")
    deposit = Decimal(row["deposit_held"] or 0)
    if body.refund_deposit and deposit > 0:
        if folio is None:
            raise HTTPException(status_code=422,
                                detail="No folio to refund the deposit from.")
        # A refund is money leaving, so it is a debit — the mirror of the
        # credit taken at check-in.
        entry_id = uuid.uuid4()
        db.execute(
            text(
                """
                INSERT INTO finance.folio_entries
                    (id, organization_id, property_id, folio_id, entry_type,
                     amount, currency, business_date, source_type,
                     source_line_key)
                VALUES (:id, :org, :prop, :folio, 'debit', :amt, :cur,
                        :bd, 'deposit_refund', :slk)
                """
            ),
            {"id": entry_id, "org": row["organization_id"],
             "prop": property_id, "folio": folio["id"], "amt": deposit,
             "cur": resolve_currency(db, stated=None,
                                     folio_ids=[folio["id"]]),
             "bd": trading_day(db, property_id),
             "slk": f"deposit_refund:{unit_id}"},
        )
        refunded = deposit
        balance += deposit

    if balance > 0 and not body.allow_outstanding_balance:
        raise HTTPException(
            status_code=422,
            detail=f"{balance} is still outstanding. Collect it, or confirm "
                   f"that the guest is leaving with a balance.",
        )

    try:
        result = check_out(db, reservation_unit_id=unit_id,
                           checked_out_by=caller.user_id,
                           # Which nights go back on sale is a calendar
                           # question, and the calendar is the hotel's.
                           business_date=local_today(db, property_id))
    except FlowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if balance > 0:
        warnings.append(
            f"Checked out with {balance} outstanding. The folio stays open "
            f"until it is settled."
        )
    elif balance < 0:
        # The mirror case, and it had no warning at all.
        #
        # A guest leaving owing money is guarded above: checkout refuses
        # unless somebody confirms it. A guest leaving having paid *too much*
        # walked out silently -- nothing posted, nobody told, and the folio
        # kept a credit balance for ever. On one day of this property's data
        # that was five checkouts and 33,000 the hotel is holding and is not
        # entitled to.
        #
        # A warning rather than a refusal: the money is already in the till
        # and blocking the door does not give it back. Refunding is a decision
        # with a person's name on it, made on the reversal screen, which is
        # where this points.
        warnings.append(
            f"This guest has paid {-balance} more than the folio was charged. "
            f"Nothing has been given back — raise a refund on the payment if "
            f"they are owed it, or post the missing charges if the folio is "
            f"short."
        )
    if body.housekeeping_notified and row["assigned_room_id"]:
        # The label on the screen says this marks the room dirty, so it has to
        # actually do it. A vacated room is dirty until somebody cleans it.
        db.execute(
            text(
                """
                INSERT INTO operations.room_condition
                    (room_id, organization_id, property_id, cleanliness)
                VALUES (:room, :org, :prop, 'dirty')
                ON CONFLICT (room_id) DO UPDATE
                   SET cleanliness = 'dirty', updated_at = now(),
                       version = operations.room_condition.version + 1
                """
            ),
            {"room": row["assigned_room_id"], "org": row["organization_id"],
             "prop": property_id},
        )

    if body.feedback_scheduled:
        warnings.append(
            "Feedback marked as scheduled. There is no mail or SMS transport "
            "yet, so nothing will be sent automatically."
        )
    if not body.key_returned:
        warnings.append("The room key was not marked as returned.")

    db.execute(
        text(
            """
            INSERT INTO booking.stay_checkouts
                (organization_id, property_id, reservation_unit_id, stay_id,
                 room_id, settled_amount, balance_at_checkout, payment_id,
                 key_returned, housekeeping_notified, feedback_scheduled,
                 deposit_refunded, notes, checked_out_by)
            VALUES (:org, :prop, :unit, :stay, :room, :settled, :balance, :pay,
                    :key, :hk, :fb, :refund, :notes, :who)
            """
        ),
        {
            "org": row["organization_id"], "prop": property_id, "unit": unit_id,
            "stay": result.stay_id,
            "room": row["assigned_room_id"], "settled": collected,
            "balance": balance, "pay": payment_id,
            "key": body.key_returned, "hk": body.housekeeping_notified,
            "fb": body.feedback_scheduled, "refund": refunded,
            "notes": body.notes, "who": caller.user_id,
        },
    )
    record_audit(
        db, action="reservation_unit.check_out", entity_type="reservation_unit",
        entity_id=str(unit_id), organization_id=row["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"room": row["room"], "collected": str(collected),
               "balance_at_checkout": str(balance),
               "deposit_refunded": str(refunded),
               "key_returned": body.key_returned},
    )
    return CompleteCheckOutOut(
        reservation_unit_id=unit_id, room=row["room"], collected=collected,
        deposit_refunded=refunded, balance_at_checkout=balance,
        nights_released=result.nights_released,
        warnings=warnings,
    )
