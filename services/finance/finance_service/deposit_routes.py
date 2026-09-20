"""Deposit Schedule API (screen 058).

A deposit schedule is a plan: how much of a booking is owed, and by when. The
money it plans for already has a home — ``post_payment`` captures a payment and
posts a credit to the folio — so nothing here moves money on its own. Collecting
against an instalment calls the ledger and then records *which* instalment the
resulting payment settled.

That is why an instalment has no ``paid_amount`` column. What has been paid is
the sum of its allocations, and every allocation points at a real payment. The
schedule therefore cannot drift from the ledger: there is only one place the
money is recorded, and this reads it.

Two things the mockup shows are deliberately not pretended here:

* **Reminders.** There is no mail or SMS transport in this system yet. A
  reminder can be scheduled, and can be marked as sent by whoever sent it, and
  the screen says which of those it is. Nothing claims an email went out.
* **Waiver approval.** Screen 042 has an approval queue but no way to raise a
  request into it. A waiver above the threshold is recorded as *requested* and
  stays unapplied until someone with ``payments:approve`` decides it. The
  balance does not move in the meantime.

Statuses are derived, never typed in. An instalment is paid when its
allocations plus any approved waiver reach its amount, partially paid when they
fall short of it, and pending when nothing has come in. Cancelled is the one
status a person sets, because only a person knows an instalment is no longer
going to be collected.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import (
    assert_org_matches_caller,
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

from .database import get_session
from .ledger import Allocation, LedgerError, post_payment
from .night_audit import local_today
from .routes import _trading_day
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

deposit_router = APIRouter(tags=["deposits"], route_class=TransactionalRoute)

DUE_RULES = ("at_booking", "fixed_date", "at_checkin")
DUE_RULE_LABELS = {
    "at_booking": "At time of booking",
    "fixed_date": "",
    "at_checkin": "At Check-in",
}
STATUS_LABELS = {
    "pending": "Pending", "partially_paid": "Partially Paid", "paid": "Paid",
    "waived": "Waived", "cancelled": "Cancelled",
}
# Above this, a waiver needs a decision from someone holding payments:approve.
# The mockup states the rule on the screen; it is enforced here so the two
# cannot disagree.
WAIVER_APPROVAL_THRESHOLD = Decimal("5000")


def _q(v) -> Decimal:
    return Decimal(v or 0).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class InstallmentIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    amount: Decimal | None = None
    percent: Decimal | None = Field(default=None, ge=0, le=100)
    due_rule: str = "fixed_date"
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=300)
    reminder_due_on: date | None = None


class InstallmentPatch(InstallmentIn):
    version: int


class AllocationOut(BaseModel):
    id: uuid.UUID
    amount: Decimal
    method: str
    reference: str | None
    received_on: date
    reason: str | None
    is_reversal: bool
    reversed: bool


class InstallmentOut(BaseModel):
    id: uuid.UUID
    seq: int
    label: str
    amount: Decimal
    percent: Decimal | None
    due_rule: str
    due_date: date | None
    due_label: str
    status: str
    status_label: str
    paid_amount: Decimal
    waived_amount: Decimal
    balance_due: Decimal
    received_on: date | None
    payment_method: str | None
    waiver_status: str
    waiver_reason: str | None
    reminder_state: str
    reminder_sent_at: str | None
    reminder_due_on: date | None
    reminder_label: str
    notes: str | None
    version: int
    allocations: list[AllocationOut]


class ScheduleTotals(BaseModel):
    booking_value: Decimal
    deposit_required: Decimal
    deposit_percent: Decimal | None
    received: Decimal
    received_percent: Decimal | None
    waived: Decimal
    balance_due: Decimal
    next_due_date: date | None
    next_due_in_days: int | None


class ScheduleOut(BaseModel):
    reservation_id: uuid.UUID
    organization_id: uuid.UUID | None
    property_id: uuid.UUID | None
    folio_id: uuid.UUID | None
    currency: str
    booking_value: Decimal
    installments: list[InstallmentOut]
    totals: ScheduleTotals
    waiver_threshold: Decimal
    can_approve_waiver: bool


class CollectIn(BaseModel):
    amount: Decimal = Field(gt=0)
    method: str = Field(min_length=1, max_length=30)
    received_on: date | None = None
    reference: str | None = Field(default=None, max_length=120)


class WaiveIn(BaseModel):
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=300)


class WaiverDecisionIn(BaseModel):
    decision: str  # approved | rejected
    reason: str | None = Field(default=None, max_length=300)


class RefundIn(BaseModel):
    allocation_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=300)


class ReminderIn(BaseModel):
    action: str  # schedule | mark_sent | clear
    due_on: date | None = None


class GenerateIn(BaseModel):
    """Build a standard schedule from percentages of the booking value."""

    booking_value: Decimal = Field(gt=0)
    folio_id: uuid.UUID | None = None
    arrival_date: date | None = None
    splits: list[InstallmentIn] = Field(min_length=1)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
_ROW_SQL = """
    SELECT i.*,
           COALESCE(a.paid, 0) AS paid_amount,
           a.last_on AS received_on,
           a.last_method AS payment_method
    FROM finance.deposit_installments i
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS paid,
               MAX(x.received_on) FILTER (WHERE x.amount > 0) AS last_on,
               (ARRAY_AGG(x.method ORDER BY x.received_on DESC, x.recorded_at DESC)
                FILTER (WHERE x.amount > 0))[1] AS last_method
        FROM finance.deposit_allocations x
        WHERE x.installment_id = i.id
    ) a ON TRUE
"""


def _due_label(row) -> str:
    """The due-date column reads as one phrase, not a date plus a code."""
    named = DUE_RULE_LABELS.get(row["due_rule"], "")
    when = row["due_date"].strftime("%d %b %Y") if row["due_date"] else None
    if named and when:
        # "At time of booking (05 Aug)" reads forwards; "18 Sep (At Check-in)"
        # reads backwards. The mockup does both, and it is right to.
        return (f"{named} ({when})" if row["due_rule"] == "at_booking"
                else f"{when} ({named})")
    return named or when or "—"


def _reminder_label(row) -> str:
    if row["reminder_state"] == "sent" and row["reminder_sent_at"]:
        return f"Sent ({row['reminder_sent_at'].strftime('%d %b %Y')})"
    if row["reminder_state"] == "scheduled" and row["reminder_due_on"]:
        return f"Scheduled ({row['reminder_due_on'].strftime('%d %b %Y')})"
    return "—"


def _effective_waiver(row) -> Decimal:
    """A waiver counts against the balance only once it needs no decision."""
    if row["waiver_status"] != "approved":
        return Decimal("0")
    return _q(row["waived_amount"])


def _derive_status(row, paid: Decimal) -> str:
    """Status follows the money; only 'cancelled' is set by a person."""
    if row["status"] == "cancelled":
        return "cancelled"
    waived = _effective_waiver(row)
    settled = paid + waived
    if waived >= _q(row["amount"]) and paid <= 0:
        return "waived"
    if settled >= _q(row["amount"]):
        return "paid"
    if settled > 0:
        return "partially_paid"
    return "pending"


def _allocations(db: Session, ids: list[uuid.UUID]) -> dict:
    if not ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT a.*, (r.id IS NOT NULL) AS reversed
            FROM finance.deposit_allocations a
            LEFT JOIN finance.deposit_allocations r ON r.reversal_of_id = a.id
            WHERE a.installment_id = ANY(:ids)
            ORDER BY a.received_on, a.recorded_at
            """
        ),
        {"ids": ids},
    ).mappings().all()
    out: dict = {}
    for r in rows:
        out.setdefault(r["installment_id"], []).append(
            AllocationOut(
                id=r["id"], amount=_q(r["amount"]), method=r["method"],
                reference=r["reference"], received_on=r["received_on"],
                reason=r["reason"], is_reversal=r["reversal_of_id"] is not None,
                reversed=bool(r["reversed"]),
            )
        )
    return out


def _out(row, allocations: list[AllocationOut]) -> InstallmentOut:
    paid = _q(row["paid_amount"])
    derived = _derive_status(row, paid)
    balance = _q(row["amount"]) - paid - _effective_waiver(row)
    return InstallmentOut(
        id=row["id"], seq=row["seq"], label=row["label"],
        amount=_q(row["amount"]), percent=row["percent"],
        due_rule=row["due_rule"], due_date=row["due_date"],
        due_label=_due_label(row),
        status=derived, status_label=STATUS_LABELS[derived],
        paid_amount=paid, waived_amount=_q(row["waived_amount"]),
        balance_due=(_q(0) if derived == "cancelled"
                     else max(balance, _q(0))),
        received_on=row["received_on"], payment_method=row["payment_method"],
        waiver_status=row["waiver_status"], waiver_reason=row["waiver_reason"],
        reminder_state=row["reminder_state"],
        reminder_sent_at=(row["reminder_sent_at"].isoformat()
                          if row["reminder_sent_at"] else None),
        reminder_due_on=row["reminder_due_on"],
        reminder_label=_reminder_label(row),
        notes=row["notes"], version=row["version"], allocations=allocations,
    )


def _may_approve(db: Session, caller: Caller, property_id: uuid.UUID) -> bool:
    """Whether this caller could decide a waiver, so the screen can say so.

    Asks the same grant query the ``payments:approve`` dependency enforces, so
    the button the screen offers and the permission the endpoint checks cannot
    come apart.
    """
    if caller.user_id is None:
        return False
    return bool(
        db.execute(
            text(_GRANT_SQL),
            {"uid": caller.user_id, "res": "payments", "act": "approve",
             "prop": property_id},
        ).first()
    )


def _schedule(
    db: Session, reservation_id: uuid.UUID, property_id: uuid.UUID,
    booking_value: Decimal, caller: Caller, include_cancelled: bool,
) -> ScheduleOut:
    rows = db.execute(
        text(_ROW_SQL + " WHERE i.reservation_id = :res ORDER BY i.seq"),
        {"res": reservation_id},
    ).mappings().all()
    allocs = _allocations(db, [r["id"] for r in rows])
    items = [_out(r, allocs.get(r["id"], [])) for r in rows]
    # Cancelled instalments are hidden by default and never counted: they are
    # no longer owed, so including them would overstate what the guest must pay.
    live = [i for i in items if i.status != "cancelled"]
    shown = items if include_cancelled else live

    required = sum((i.amount for i in live), Decimal("0"))
    received = sum((i.paid_amount for i in live), Decimal("0"))
    waived = sum((i.waived_amount for i in live
                  if i.waiver_status == "approved"), Decimal("0"))
    outstanding = sum((i.balance_due for i in live), Decimal("0"))

    # The property's own calendar date -- not the trading day, and not UTC.
    # A due date is a calendar promise ("pay by the 20th"), so counting the
    # days to it is a calendar question and the answer belongs to the hotel's
    # timezone, whatever day the ledger happens to have open.
    #
    # UTC lags India by five and a half hours, so between midnight and 05:30
    # it still reported yesterday: an instalment due on the 21st read "due in
    # 3 days" to a clerk for whom it was already the 19th and the answer was
    # 2. A day more than you have is the wrong direction for a payment
    # deadline to be wrong in.
    today = local_today(db, property_id)
    upcoming = sorted(i.due_date for i in live
                      if i.due_date and i.balance_due > 0)
    next_due = upcoming[0] if upcoming else None

    head = rows[0] if rows else None
    return ScheduleOut(
        reservation_id=reservation_id,
        organization_id=head["organization_id"] if head else None,
        property_id=head["property_id"] if head else None,
        folio_id=next((r["folio_id"] for r in rows if r["folio_id"]), None),
        currency="INR",
        booking_value=_q(booking_value),
        installments=shown,
        totals=ScheduleTotals(
            booking_value=_q(booking_value),
            deposit_required=_q(required),
            deposit_percent=(_q(required * 100 / booking_value)
                             if booking_value > 0 else None),
            received=_q(received),
            # The mockup's "51%" is of what was *asked for*, not of the booking.
            received_percent=(_q(received * 100 / required)
                              if required > 0 else None),
            waived=_q(waived),
            balance_due=_q(outstanding),
            next_due_date=next_due,
            next_due_in_days=(next_due - today).days if next_due else None,
        ),
        waiver_threshold=WAIVER_APPROVAL_THRESHOLD,
        can_approve_waiver=_may_approve(db, caller, property_id),
    )


def _load(db: Session, installment_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text(_ROW_SQL + " WHERE i.id = :id AND i.property_id = :prop"),
        {"id": installment_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Installment not found")
    return row


def _refreshed(db: Session, installment_id: uuid.UUID, property_id: uuid.UUID):
    row = _load(db, installment_id, property_id)
    return _out(row, _allocations(db, [installment_id]).get(installment_id, []))


@deposit_router.get(
    "/reservations/{reservation_id}/deposit-schedule", response_model=ScheduleOut
)
def get_schedule(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    booking_value: Decimal = Query(Decimal("0")),
    include_cancelled: bool = Query(False),
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """The schedule, its allocations, and the totals the KPI cards show.

    ``booking_value`` comes from booking-core (the reservation's room revenue);
    finance does not own that number, so it is passed in rather than guessed at.
    """
    assert_property_in_org(db, caller, property_id)
    return _schedule(db, reservation_id, property_id,
                     Decimal(booking_value), caller, include_cancelled)


# --------------------------------------------------------------------------
# Building the schedule
# --------------------------------------------------------------------------
def _next_seq(db: Session, reservation_id: uuid.UUID) -> int:
    return int(
        db.execute(
            text("SELECT COALESCE(MAX(seq), 0) + 1 "
                 "FROM finance.deposit_installments WHERE reservation_id = :r"),
            {"r": reservation_id},
        ).scalar_one()
    )


def _resolve_amount(body: InstallmentIn, booking_value: Decimal) -> Decimal:
    """An instalment is either a stated amount or a percentage of the booking."""
    if body.amount is not None and body.amount > 0:
        return _q(body.amount)
    if body.percent is not None and body.percent > 0:
        if booking_value <= 0:
            raise HTTPException(
                status_code=422,
                detail="A percentage needs a booking value to be a percentage of.",
            )
        return _q(booking_value * Decimal(body.percent) / Decimal(100))
    raise HTTPException(status_code=422, detail="Enter an amount or a percentage.")


def _check_due(body: InstallmentIn) -> None:
    if body.due_rule not in DUE_RULES:
        raise HTTPException(status_code=422, detail="Unknown due rule")
    if body.due_rule == "fixed_date" and body.due_date is None:
        raise HTTPException(status_code=422, detail="Pick a due date.")


@deposit_router.post(
    "/reservations/{reservation_id}/deposit-schedule",
    response_model=ScheduleOut, status_code=status.HTTP_201_CREATED,
)
def generate_schedule(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    organization_id: uuid.UUID,
    body: GenerateIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Create a schedule from percentage splits, for a reservation with none.

    Refuses to run over an existing schedule: rebuilding one would have to
    decide what to do with money already collected against it, and that is a
    decision for whoever is looking at the screen, not a default.
    """
    assert_org_matches_caller(caller, organization_id)
    assert_property_in_org(db, caller, property_id)
    if db.execute(
        text("SELECT 1 FROM finance.deposit_installments "
             "WHERE reservation_id = :r LIMIT 1"),
        {"r": reservation_id},
    ).first():
        raise HTTPException(
            status_code=409,
            detail="This reservation already has a schedule. "
                   "Add or edit instalments instead.",
        )

    created = []
    for n, item in enumerate(body.splits, start=1):
        # 'at_checkin' takes its date from the reservation's arrival; the check
        # below is what makes a fixed instalment supply its own.
        due = body.arrival_date if item.due_rule == "at_checkin" else item.due_date
        if item.due_rule != "at_checkin":
            _check_due(item)
        amount = _resolve_amount(item, Decimal(body.booking_value))
        row_id = uuid.uuid4()
        db.execute(
            text(
                """
                INSERT INTO finance.deposit_installments
                    (id, organization_id, property_id, reservation_id, folio_id,
                     seq, label, amount, percent, due_rule, due_date, notes,
                     reminder_due_on, reminder_state, created_by, updated_by)
                VALUES (:id, :org, :prop, :res, :folio, :seq, :label, :amt,
                        :pct, :rule, :due, :notes, :rem,
                        CASE WHEN CAST(:rem AS date) IS NULL
                             THEN 'none' ELSE 'scheduled' END,
                        :who, :who)
                """
            ),
            {
                "id": row_id, "org": organization_id, "prop": property_id,
                "res": reservation_id, "folio": body.folio_id, "seq": n,
                "label": item.label, "amt": amount, "pct": item.percent,
                "rule": item.due_rule, "due": due, "notes": item.notes,
                "rem": item.reminder_due_on, "who": caller.user_id,
            },
        )
        created.append({"id": str(row_id), "label": item.label,
                        "amount": str(amount)})

    record_audit(
        db, action="deposit_schedule.generate", entity_type="reservation",
        entity_id=str(reservation_id), organization_id=organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"installments": created,
               "booking_value": str(body.booking_value)},
    )
    return _schedule(db, reservation_id, property_id,
                     Decimal(body.booking_value), caller, False)


@deposit_router.post(
    "/reservations/{reservation_id}/deposit-schedule/installments",
    response_model=InstallmentOut, status_code=status.HTTP_201_CREATED,
)
def add_installment(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    organization_id: uuid.UUID,
    body: InstallmentIn,
    booking_value: Decimal = Query(Decimal("0")),
    folio_id: uuid.UUID | None = Query(None),
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Add one instalment to the schedule."""
    assert_org_matches_caller(caller, organization_id)
    assert_property_in_org(db, caller, property_id)
    _check_due(body)
    amount = _resolve_amount(body, Decimal(booking_value))
    row_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.deposit_installments
                (id, organization_id, property_id, reservation_id, folio_id,
                 seq, label, amount, percent, due_rule, due_date, notes,
                 reminder_due_on, reminder_state, created_by, updated_by)
            VALUES (:id, :org, :prop, :res,
                    COALESCE(CAST(:folio AS uuid),
                             (SELECT folio_id FROM finance.deposit_installments
                               WHERE reservation_id = :res
                                 AND folio_id IS NOT NULL LIMIT 1)),
                    :seq, :label, :amt, :pct, :rule, :due, :notes, :rem,
                    CASE WHEN CAST(:rem AS date) IS NULL
                         THEN 'none' ELSE 'scheduled' END,
                    :who, :who)
            """
        ),
        {
            "id": row_id, "org": organization_id, "prop": property_id,
            "res": reservation_id, "folio": folio_id,
            "seq": _next_seq(db, reservation_id), "label": body.label,
            "amt": amount, "pct": body.percent, "rule": body.due_rule,
            "due": body.due_date, "notes": body.notes,
            "rem": body.reminder_due_on, "who": caller.user_id,
        },
    )
    record_audit(
        db, action="deposit_installment.create",
        entity_type="deposit_installment", entity_id=str(row_id),
        organization_id=organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={"label": body.label, "amount": str(amount),
               "due_date": str(body.due_date) if body.due_date else None},
    )
    return _refreshed(db, row_id, property_id)


@deposit_router.put(
    "/deposit-installments/{installment_id}", response_model=InstallmentOut
)
def update_installment(
    installment_id: uuid.UUID,
    property_id: uuid.UUID,
    body: InstallmentPatch,
    booking_value: Decimal = Query(Decimal("0")),
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Edit an instalment.

    The amount cannot drop below what has already been collected against it:
    that would make the schedule claim an overpayment the ledger disagrees with.
    """
    assert_property_in_org(db, caller, property_id)
    before = _load(db, installment_id, property_id)
    _check_due(body)
    amount = _resolve_amount(body, Decimal(booking_value))
    paid = _q(before["paid_amount"])
    if amount < paid:
        raise HTTPException(
            status_code=422,
            detail=f"{paid} has already been collected against this "
                   f"instalment; it cannot be reduced below that.",
        )
    updated = db.execute(
        text(
            """
            UPDATE finance.deposit_installments
               SET label = :label, amount = :amt, percent = :pct,
                   due_rule = :rule, due_date = :due, notes = :notes,
                   updated_by = :who, updated_at = now(), version = version + 1
             WHERE id = :id AND property_id = :prop AND version = :ver
            RETURNING id
            """
        ),
        {
            "id": installment_id, "prop": property_id, "ver": body.version,
            "label": body.label, "amt": amount, "pct": body.percent,
            "rule": body.due_rule, "due": body.due_date, "notes": body.notes,
            "who": caller.user_id,
        },
    ).first()
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Someone else changed this instalment. Reload and try again.",
        )
    record_audit(
        db, action="deposit_installment.update",
        entity_type="deposit_installment", entity_id=str(installment_id),
        organization_id=before["organization_id"], property_id=property_id,
        actor_subject=caller.subject,
        before={"label": before["label"], "amount": str(before["amount"]),
                "due_date": (str(before["due_date"])
                             if before["due_date"] else None)},
        after={"label": body.label, "amount": str(amount),
               "due_date": str(body.due_date) if body.due_date else None},
    )
    return _refreshed(db, installment_id, property_id)


@deposit_router.post(
    "/deposit-installments/{installment_id}/cancel", response_model=InstallmentOut
)
def cancel_installment(
    installment_id: uuid.UUID,
    property_id: uuid.UUID,
    version: int = Query(...),
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Stop collecting an instalment.

    Cancelling is not deleting: an instalment the guest was told about stays on
    the schedule behind the "show cancelled" toggle. One with money against it
    has to be refunded first, or that payment would be left allocated to
    something nobody owes any more.
    """
    assert_property_in_org(db, caller, property_id)
    before = _load(db, installment_id, property_id)
    if _q(before["paid_amount"]) > 0:
        raise HTTPException(
            status_code=422,
            detail="Refund what was collected against this instalment before "
                   "cancelling it.",
        )
    done = db.execute(
        text(
            "UPDATE finance.deposit_installments SET status = 'cancelled', "
            "updated_by = :who, updated_at = now(), version = version + 1 "
            "WHERE id = :id AND property_id = :prop AND version = :ver "
            "RETURNING id"
        ),
        {"id": installment_id, "prop": property_id, "ver": version,
         "who": caller.user_id},
    ).first()
    if done is None:
        raise HTTPException(status_code=409, detail="Reload and try again.")
    record_audit(
        db, action="deposit_installment.cancel",
        entity_type="deposit_installment", entity_id=str(installment_id),
        organization_id=before["organization_id"], property_id=property_id,
        actor_subject=caller.subject,
        before={"status": before["status"]}, after={"status": "cancelled"},
    )
    return _refreshed(db, installment_id, property_id)


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------
@deposit_router.post(
    "/deposit-installments/{installment_id}/collect", response_model=InstallmentOut
)
def collect(
    installment_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CollectIn,
    folio_id: uuid.UUID | None = Query(None),
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Take a payment against an instalment.

    The money goes through ``post_payment`` — the same path every other payment
    takes — so it lands on the folio as a credit and appears in Payments. The
    row written here only records which instalment it settled.
    """
    assert_property_in_org(db, caller, property_id)
    row = _load(db, installment_id, property_id)
    if row["status"] == "cancelled":
        raise HTTPException(status_code=422,
                            detail="This instalment was cancelled.")
    outstanding = (_q(row["amount"]) - _q(row["paid_amount"])
                   - _effective_waiver(row))
    if _q(body.amount) > outstanding:
        raise HTTPException(
            status_code=422,
            detail=f"Only {outstanding} is outstanding on this instalment.",
        )

    target_folio = folio_id or row["folio_id"]
    if target_folio is None:
        raise HTTPException(
            status_code=422,
            detail="This reservation has no folio yet, so there is nowhere to "
                   "post the payment.",
        )
    # The day the PROPERTY is trading, not the database server's calendar
    # date. CURRENT_DATE is UTC, which is neither: it is not the hotel's
    # today, and it has no relationship at all to which day the ledger has
    # open. It can miss in both directions. In India it runs BEHIND the
    # property from midnight until 05:30, so a deposit collected at 00:38 IST
    # on the 19th was stamped the 18th; and it runs AHEAD of the trading day
    # whenever the night audit is lagging, stamping a deposit onto a day the
    # ledger has not opened yet. Either way the money lands on a business
    # date the desk is not looking at, and the drawer that took it does not
    # count it.
    received = body.received_on or _trading_day(db, property_id)
    try:
        result = post_payment(
            db,
            organization_id=row["organization_id"],
            property_id=property_id,
            method=body.method,
            business_date=received,
            allocations=[Allocation(folio_id=target_folio,
                                    amount=_q(body.amount))],
            # A deposit paid in cash goes into the same drawer as any other
            # cash, and this route never said so -- the instalment was
            # settled, the folio credited, and the till held money its shift
            # did not expect. post_payment now refuses cash with no drawer
            # named, so naming it here is what keeps cash deposits possible
            # as well as what makes them count.
            cashier_shift_id=db.execute(
                text("SELECT id FROM finance.cashier_shifts "
                     "WHERE cashier_id = :u AND property_id = :p "
                     "AND status = 'open'"),
                {"u": caller.user_id, "p": property_id},
            ).scalar(),
        )
    except LedgerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.execute(
        text(
            """
            INSERT INTO finance.deposit_allocations
                (organization_id, property_id, installment_id, payment_id,
                 folio_entry_id, amount, method, reference, received_on,
                 recorded_by)
            VALUES (:org, :prop, :inst, :pay, :entry, :amt, :method, :ref,
                    :on, :who)
            """
        ),
        {
            "org": row["organization_id"], "prop": property_id,
            "inst": installment_id, "pay": result.payment_id,
            "entry": (result.credit_entry_ids[0]
                      if result.credit_entry_ids else None),
            "amt": _q(body.amount), "method": body.method,
            "ref": body.reference, "on": received, "who": caller.user_id,
        },
    )
    # Remember the folio once we know which one it is, so later collections on
    # this reservation do not have to be told again.
    if row["folio_id"] is None:
        db.execute(
            text("UPDATE finance.deposit_installments SET folio_id = :f "
                 "WHERE reservation_id = :r AND folio_id IS NULL"),
            {"f": target_folio, "r": row["reservation_id"]},
        )
    record_audit(
        db, action="deposit_installment.collect",
        entity_type="deposit_installment", entity_id=str(installment_id),
        organization_id=row["organization_id"], property_id=property_id,
        actor_subject=caller.subject,
        after={"amount": str(_q(body.amount)), "method": body.method,
               "payment_id": str(result.payment_id)},
    )
    return _refreshed(db, installment_id, property_id)


@deposit_router.post(
    "/deposit-installments/{installment_id}/refund", response_model=InstallmentOut
)
def refund(
    installment_id: uuid.UUID,
    property_id: uuid.UUID,
    body: RefundIn,
    caller: Caller = Depends(require_permission("payments", "cancel")),
    db: Session = Depends(get_session),
):
    """Reverse a collection, in full or in part.

    The reversal is stored as a negative allocation pointing at the payment it
    undoes, and a debit is posted to the folio so the guest's balance moves with
    it. The original row stays: a refund is a second event, not an erasure of
    the first.
    """
    assert_property_in_org(db, caller, property_id)
    row = _load(db, installment_id, property_id)
    original = db.execute(
        text(
            """
            SELECT a.*, (r.id IS NOT NULL) AS already_reversed
            FROM finance.deposit_allocations a
            LEFT JOIN finance.deposit_allocations r ON r.reversal_of_id = a.id
            WHERE a.id = :id AND a.installment_id = :inst
            """
        ),
        {"id": body.allocation_id, "inst": installment_id},
    ).mappings().first()
    if original is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if original["amount"] <= 0:
        raise HTTPException(status_code=422,
                            detail="That row is itself a refund.")
    if original["already_reversed"]:
        raise HTTPException(status_code=422,
                            detail="That payment has already been refunded.")
    if _q(body.amount) > _q(original["amount"]):
        raise HTTPException(
            status_code=422,
            detail=f"The payment was {_q(original['amount'])}; "
                   f"a refund cannot exceed it.",
        )

    # Same rule as the collect path above, and for the same reason: this is a
    # business date on a ledger entry, so it follows the property's trading
    # day rather than UTC's idea of today.
    today = _trading_day(db, property_id)
    entry_id = uuid.uuid4() if row["folio_id"] is not None else None
    if entry_id is not None:
        # Money leaving is a debit: it puts the amount back on the balance.
        db.execute(
            text(
                """
                INSERT INTO finance.folio_entries
                    (id, organization_id, property_id, folio_id, entry_type,
                     amount, currency, business_date, source_type, source_id,
                     source_line_key)
                VALUES (:id, :org, :prop, :folio, 'debit', :amt, 'INR', :bd,
                        'deposit_refund', :src, :slk)
                """
            ),
            {
                "id": entry_id, "org": row["organization_id"],
                "prop": property_id, "folio": row["folio_id"],
                "amt": _q(body.amount), "bd": today,
                "src": str(body.allocation_id),
                "slk": f"deposit_refund:{body.allocation_id}",
            },
        )
    db.execute(
        text(
            """
            INSERT INTO finance.deposit_allocations
                (organization_id, property_id, installment_id, payment_id,
                 folio_entry_id, amount, method, reference, received_on,
                 reversal_of_id, reason, recorded_by)
            VALUES (:org, :prop, :inst, :pay, :entry, :amt, :method, :ref, :on,
                    :rev, :reason, :who)
            """
        ),
        {
            "org": row["organization_id"], "prop": property_id,
            "inst": installment_id, "pay": original["payment_id"],
            "entry": entry_id, "amt": -_q(body.amount),
            "method": original["method"], "ref": original["reference"],
            "on": today, "rev": body.allocation_id, "reason": body.reason,
            "who": caller.user_id,
        },
    )
    # Tell finance.refunds about it. That table is the ledger's answer to
    # "how much of this payment can still be given back", and a refund it
    # cannot see is one the ordinary reversal route will happily make again.
    # No provider call: the deposit route moves money by its own arrangement,
    # so there is no gateway reference to record -- but the amount counts
    # against the payment either way, which is the whole point.
    if original["payment_id"] is not None:
        db.execute(
            text(
                """
                INSERT INTO finance.refunds
                    (id, organization_id, property_id, payment_id, amount,
                     currency, reason, status, cashier_shift_id, method)
                VALUES (:id, :org, :prop, :pay, :amt, 'INR', :reason,
                        'succeeded', :shift, :method)
                """
            ),
            {
                "id": uuid.uuid4(), "org": row["organization_id"],
                "prop": property_id, "pay": original["payment_id"],
                "amt": _q(body.amount),
                "reason": f"Deposit refund: {body.reason}"[:200],
                # The drawer this cashier has open, when they have one. Not
                # required here, unlike /refunds -- this route has never
                # demanded it and refusing now would stop a working flow.
                # Recording it when it exists is still better than never.
                "shift": db.execute(
                    text("SELECT id FROM finance.cashier_shifts "
                         "WHERE cashier_id = :u AND property_id = :p "
                         "AND status = 'open'"),
                    {"u": caller.user_id, "p": property_id},
                ).scalar(),
                "method": original["method"],
            },
        )

    record_audit(
        db, action="deposit_installment.refund",
        entity_type="deposit_installment", entity_id=str(installment_id),
        organization_id=row["organization_id"], property_id=property_id,
        actor_subject=caller.subject, reason=body.reason,
        before={"allocation": str(body.allocation_id),
                "amount": str(_q(original["amount"]))},
        after={"refunded": str(_q(body.amount))},
    )
    return _refreshed(db, installment_id, property_id)


# --------------------------------------------------------------------------
# Waivers
# --------------------------------------------------------------------------
@deposit_router.post(
    "/deposit-installments/{installment_id}/waive", response_model=InstallmentOut
)
def waive(
    installment_id: uuid.UUID,
    property_id: uuid.UUID,
    body: WaiveIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Ask to write off part of an instalment.

    Under the threshold, whoever can edit the schedule can waive outright. Over
    it, the request is recorded and left *unapplied* until someone with
    ``payments:approve`` decides it — the balance does not move in the meantime,
    because a waiver that has not been approved has not happened.
    """
    assert_property_in_org(db, caller, property_id)
    row = _load(db, installment_id, property_id)
    if row["status"] == "cancelled":
        raise HTTPException(status_code=422,
                            detail="This instalment was cancelled.")
    if row["waiver_status"] == "requested":
        raise HTTPException(
            status_code=422,
            detail="A waiver is already awaiting a decision on this instalment.",
        )
    outstanding = _q(row["amount"]) - _q(row["paid_amount"])
    if _q(body.amount) > outstanding:
        raise HTTPException(
            status_code=422,
            detail=f"Only {outstanding} is outstanding on this instalment.",
        )

    approves_now = (_q(body.amount) <= WAIVER_APPROVAL_THRESHOLD
                    or _may_approve(db, caller, property_id))
    new_status = "approved" if approves_now else "requested"
    db.execute(
        text(
            """
            UPDATE finance.deposit_installments
               SET waived_amount = :amt, waiver_status = :st,
                   waiver_reason = :reason, waiver_requested_by = :who,
                   waiver_requested_at = now(), updated_by = :who,
                   updated_at = now(), version = version + 1
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": installment_id, "prop": property_id, "amt": _q(body.amount),
         "st": new_status, "reason": body.reason, "who": caller.user_id},
    )
    record_audit(
        db, action=f"deposit_waiver.{new_status}",
        entity_type="deposit_installment", entity_id=str(installment_id),
        organization_id=row["organization_id"], property_id=property_id,
        actor_subject=caller.subject, reason=body.reason,
        after={"amount": str(_q(body.amount)), "status": new_status,
               "threshold": str(WAIVER_APPROVAL_THRESHOLD)},
    )
    return _refreshed(db, installment_id, property_id)


@deposit_router.post(
    "/deposit-installments/{installment_id}/waiver-decision",
    response_model=InstallmentOut,
)
def decide_waiver(
    installment_id: uuid.UUID,
    property_id: uuid.UUID,
    body: WaiverDecisionIn,
    caller: Caller = Depends(require_permission("payments", "approve")),
    db: Session = Depends(get_session),
):
    """Approve or reject a waiver that was over the threshold."""
    assert_property_in_org(db, caller, property_id)
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="Unknown decision")
    row = _load(db, installment_id, property_id)
    if row["waiver_status"] != "requested":
        raise HTTPException(status_code=422,
                            detail="There is no waiver awaiting a decision.")
    db.execute(
        text(
            """
            UPDATE finance.deposit_installments
               SET waiver_status = CAST(:st AS varchar),
                   -- A rejected waiver leaves nothing behind: the amount goes
                   -- back to zero so the balance cannot quietly keep it.
                   waived_amount = CASE WHEN CAST(:st AS varchar) = 'approved'
                                        THEN waived_amount ELSE 0 END,
                   updated_by = :who, updated_at = now(), version = version + 1
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": installment_id, "prop": property_id, "st": body.decision,
         "who": caller.user_id},
    )
    record_audit(
        db, action=f"deposit_waiver.{body.decision}",
        entity_type="deposit_installment", entity_id=str(installment_id),
        organization_id=row["organization_id"], property_id=property_id,
        actor_subject=caller.subject, reason=body.reason,
        before={"waiver_status": "requested",
                "amount": str(_q(row["waived_amount"]))},
        after={"waiver_status": body.decision},
    )
    return _refreshed(db, installment_id, property_id)


# --------------------------------------------------------------------------
# Reminders
# --------------------------------------------------------------------------
@deposit_router.post(
    "/deposit-installments/{installment_id}/reminder",
    response_model=InstallmentOut,
)
def set_reminder(
    installment_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ReminderIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Record a reminder's state.

    Nothing is sent from here — this system has no mail or SMS transport yet.
    "Schedule" notes the date a reminder is due; "mark sent" records that
    someone sent it. The screen labels each accordingly rather than implying a
    message went out on its own.
    """
    assert_property_in_org(db, caller, property_id)
    row = _load(db, installment_id, property_id)
    if body.action == "schedule":
        if body.due_on is None:
            raise HTTPException(status_code=422, detail="Pick a reminder date.")
        sql = ("SET reminder_state = 'scheduled', reminder_due_on = :d, "
               "reminder_sent_at = NULL")
    elif body.action == "mark_sent":
        sql = "SET reminder_state = 'sent', reminder_sent_at = now()"
    elif body.action == "clear":
        sql = ("SET reminder_state = 'none', reminder_due_on = NULL, "
               "reminder_sent_at = NULL")
    else:
        raise HTTPException(status_code=422, detail="Unknown reminder action")

    db.execute(
        text(
            f"UPDATE finance.deposit_installments {sql}, updated_at = now(), "
            f"version = version + 1 WHERE id = :id AND property_id = :prop"
        ),
        ({"id": installment_id, "prop": property_id, "d": body.due_on}
         if body.action == "schedule"
         else {"id": installment_id, "prop": property_id}),
    )
    record_audit(
        db, action=f"deposit_reminder.{body.action}",
        entity_type="deposit_installment", entity_id=str(installment_id),
        organization_id=row["organization_id"], property_id=property_id,
        actor_subject=caller.subject,
        after={"action": body.action,
               "due_on": str(body.due_on) if body.due_on else None},
    )
    return _refreshed(db, installment_id, property_id)
