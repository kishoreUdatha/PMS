"""Expense vouchers -- money going out that is not a guest refund.

A voucher is raised, decided, and paid, in that order:

    pending_approval --approve--> approved --pay--> paid
           |                         |
           +--reject--> rejected     +--cancel--> cancelled
           +--cancel--> cancelled

**Deciding is a separate permission from raising.** Approving or rejecting
needs ``payments:approve``; raising needs ``payments:create``; paying or
cancelling needs ``payments:edit``. A voucher records who decided it and when,
which is the question an auditor asks.

**Only an undecided voucher can be edited.** Once approved, the amount that was
approved is the amount on the voucher. A wrong approved voucher is cancelled
and raised again, so nothing approved is ever changed underneath its approver.

**A rejection says why.** A voucher refused with no reason is one the person
who raised it cannot fix.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import (
    _GRANT_SQL, Caller, assert_property_in_org, build_authz,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

expense_router = APIRouter(
    prefix="/expenses", tags=["expenses"], route_class=TransactionalRoute
)

CATEGORIES = {
    "maintenance": "Maintenance & repairs", "utilities": "Utilities",
    "housekeeping_supplies": "Housekeeping supplies",
    "food_beverage": "Food & beverage purchases", "staff": "Staff & wages",
    "marketing": "Marketing", "commission": "Commission",
    "transport": "Transport", "office": "Office & admin", "other": "Other",
}
METHODS = {
    "cash": "Cash", "bank_transfer": "Bank transfer", "upi": "UPI",
    "card": "Card", "cheque": "Cheque",
}
STATUS_LABELS = {
    "pending_approval": "Pending approval", "approved": "Approved",
    "rejected": "Rejected", "paid": "Paid", "cancelled": "Cancelled",
}

#: action -> (statuses it can leave, status it goes to, permission it needs)
TRANSITIONS = {
    "approve": ({"pending_approval"}, "approved", "approve"),
    "reject": ({"pending_approval"}, "rejected", "approve"),
    "pay": ({"approved"}, "paid", "edit"),
    "cancel": ({"pending_approval", "approved"}, "cancelled", "edit"),
}


class VoucherIn(BaseModel):
    expense_date: date
    payee: str = Field(min_length=1, max_length=200)
    category: str
    description: str | None = Field(None, max_length=500)
    amount: Decimal = Field(gt=0)
    tax_amount: Decimal = Field(default=Decimal(0), ge=0)
    method: str = "cash"
    reference: str | None = Field(None, max_length=100)
    room_id: uuid.UUID | None = None


class DecisionIn(BaseModel):
    action: str
    note: str | None = Field(None, max_length=300)


class Voucher(BaseModel):
    id: uuid.UUID
    voucher_no: str
    expense_date: date
    payee: str
    category: str
    category_label: str
    description: str | None
    amount: Decimal
    tax_amount: Decimal
    total: Decimal
    method: str
    method_label: str
    reference: str | None
    room_id: uuid.UUID | None
    room_code: str | None
    status: str
    status_label: str
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    paid_at: datetime | None
    created_by_name: str | None
    created_at: datetime


class Choice(BaseModel):
    value: str
    label: str


class RoomChoice(BaseModel):
    id: uuid.UUID
    code: str


class VoucherList(BaseModel):
    rows: list[Voucher]
    total: int
    #: Voucher totals (amount + tax) per status, over the rows returned.
    totals: dict[str, Decimal]
    categories: list[Choice]
    methods: list[Choice]
    statuses: list[Choice]
    rooms: list[RoomChoice]
    can_create: bool
    can_edit: bool
    can_approve: bool


_ROW_SQL = """
    SELECT v.*, rm.code AS room_code,
           cu.display_name AS created_by_name, du.display_name AS decided_by_name
    FROM finance.expense_vouchers v
    LEFT JOIN property.rooms rm ON rm.id = v.room_id
    LEFT JOIN iam.users cu ON cu.id = v.created_by
    LEFT JOIN iam.users du ON du.id = v.decided_by
"""


def _out(r) -> Voucher:
    return Voucher(
        id=r["id"], voucher_no=f"EV-{r['voucher_number']}",
        expense_date=r["expense_date"], payee=r["payee"],
        category=r["category"],
        category_label=CATEGORIES.get(r["category"], r["category"]),
        description=r["description"], amount=r["amount"],
        tax_amount=r["tax_amount"], total=r["amount"] + r["tax_amount"],
        method=r["method"], method_label=METHODS.get(r["method"], r["method"]),
        reference=r["reference"], room_id=r["room_id"], room_code=r["room_code"],
        status=r["status"], status_label=STATUS_LABELS.get(r["status"], r["status"]),
        decided_by_name=r["decided_by_name"], decided_at=r["decided_at"],
        decision_note=r["decision_note"], paid_at=r["paid_at"],
        created_by_name=r["created_by_name"], created_at=r["created_at"],
    )


def _get(db: Session, voucher_id: uuid.UUID, property_id: uuid.UUID) -> Voucher:
    row = db.execute(
        text(_ROW_SQL + " WHERE v.id = :i AND v.property_id = :p"),
        {"i": voucher_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Voucher not found.")
    return _out(row)


def _may(db: Session, caller: Caller, property_id: uuid.UUID, action: str) -> bool:
    if caller.is_service:
        return True
    return db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "payments", "act": action,
         "prop": property_id},
    ).first() is not None


def _validate(db: Session, property_id: uuid.UUID, body: VoucherIn) -> uuid.UUID:
    body.payee = body.payee.strip()
    if not body.payee:
        raise HTTPException(status_code=422, detail="Name who was paid.")
    if body.category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown expense category.")
    if body.method not in METHODS:
        raise HTTPException(status_code=422, detail="Unknown payment method.")
    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar()
    if org is None:
        raise HTTPException(status_code=404, detail="Property not found.")
    if body.room_id and db.execute(
        text("SELECT 1 FROM property.rooms WHERE id = :r AND property_id = :p"),
        {"r": body.room_id, "p": property_id},
    ).first() is None:
        raise HTTPException(status_code=422, detail="That room is not at this property.")
    return org


@expense_router.get("", response_model=VoucherList)
def list_vouchers(
    property_id: uuid.UUID,
    status: str | None = Query(None),
    category: str | None = Query(None),
    q: str | None = Query(None),
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """The property's vouchers, newest expense first."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(_ROW_SQL + """
            WHERE v.property_id = :p
              AND (CAST(:st AS text) IS NULL OR v.status = CAST(:st AS text))
              AND (CAST(:cat AS text) IS NULL OR v.category = CAST(:cat AS text))
              AND (CAST(:q AS text) IS NULL
                   OR v.payee ILIKE '%' || CAST(:q AS text) || '%'
                   OR COALESCE(v.description, '') ILIKE '%' || CAST(:q AS text) || '%'
                   OR ('EV-' || v.voucher_number) ILIKE '%' || CAST(:q AS text) || '%')
            ORDER BY v.expense_date DESC, v.voucher_number DESC
            LIMIT 500
        """),
        {"p": property_id, "st": status or None, "cat": category or None,
         "q": (q or "").strip() or None},
    ).mappings().all()
    out = [_out(r) for r in rows]
    rooms = [
        RoomChoice(id=r[0], code=r[1])
        for r in db.execute(
            text("SELECT id, code FROM property.rooms "
                 "WHERE property_id = :p AND status = 'active' ORDER BY code"),
            {"p": property_id})
    ]
    return VoucherList(
        rows=out, total=len(out),
        totals={k: sum((v.total for v in out if v.status == k), Decimal(0))
                for k in STATUS_LABELS},
        categories=[Choice(value=k, label=v) for k, v in CATEGORIES.items()],
        methods=[Choice(value=k, label=v) for k, v in METHODS.items()],
        statuses=[Choice(value=k, label=v) for k, v in STATUS_LABELS.items()],
        rooms=rooms,
        can_create=_may(db, caller, property_id, "create"),
        can_edit=_may(db, caller, property_id, "edit"),
        can_approve=_may(db, caller, property_id, "approve"),
    )


@expense_router.post("", response_model=Voucher, status_code=201)
def create_voucher(
    property_id: uuid.UUID,
    body: VoucherIn,
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Raise a voucher. It starts pending approval."""
    assert_property_in_org(db, caller, property_id)
    org = _validate(db, property_id, body)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(CAST(:k AS text)))"),
               {"k": f"expense-voucher:{property_id}"})
    number = db.execute(
        text("SELECT COALESCE(max(voucher_number), 1000) + 1 "
             "FROM finance.expense_vouchers WHERE property_id = :p"),
        {"p": property_id},
    ).scalar_one()
    vid = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO finance.expense_vouchers
                (id, organization_id, property_id, voucher_number, expense_date,
                 payee, category, description, amount, tax_amount, method,
                 reference, room_id, created_by, updated_by)
            VALUES (:id, :org, :p, :n, :expense_date, :payee, :category,
                    :description, :amount, :tax_amount, :method, :reference,
                    :room_id, :who, :who)
        """),
        {"id": vid, "org": org, "p": property_id, "n": number,
         "who": caller.user_id, **body.model_dump()},
    )
    record_audit(
        db, action="expense_voucher.created", entity_type="expense_voucher",
        entity_id=str(vid), organization_id=org, property_id=property_id,
        actor_subject=caller.subject,
        after={"voucher": f"EV-{number}", "payee": body.payee,
               "amount": str(body.amount + body.tax_amount)},
    )
    return _get(db, vid, property_id)


@expense_router.patch("/{voucher_id}", response_model=Voucher)
def update_voucher(
    voucher_id: uuid.UUID,
    property_id: uuid.UUID,
    body: VoucherIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Correct a voucher that nobody has decided yet."""
    assert_property_in_org(db, caller, property_id)
    before = db.execute(
        text("SELECT * FROM finance.expense_vouchers "
             "WHERE id = :i AND property_id = :p FOR UPDATE"),
        {"i": voucher_id, "p": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Voucher not found.")
    if before["status"] != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail="Only a voucher pending approval can be edited. Cancel it "
                   "and raise a new one instead.")
    org = _validate(db, property_id, body)
    db.execute(
        text("""
            UPDATE finance.expense_vouchers SET
                expense_date = :expense_date, payee = :payee, category = :category,
                description = :description, amount = :amount,
                tax_amount = :tax_amount, method = :method, reference = :reference,
                room_id = :room_id, updated_by = :who, updated_at = now(),
                version = version + 1
            WHERE id = :id
        """),
        {"id": voucher_id, "who": caller.user_id, **body.model_dump()},
    )
    record_audit(
        db, action="expense_voucher.updated", entity_type="expense_voucher",
        entity_id=str(voucher_id), organization_id=org, property_id=property_id,
        actor_subject=caller.subject,
        before={"payee": before["payee"],
                "amount": str(before["amount"] + before["tax_amount"])},
        after={"payee": body.payee, "amount": str(body.amount + body.tax_amount)},
    )
    return _get(db, voucher_id, property_id)


@expense_router.post("/{voucher_id}/decision", response_model=Voucher)
def decide_voucher(
    voucher_id: uuid.UUID,
    property_id: uuid.UUID,
    body: DecisionIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Approve, reject, pay or cancel."""
    assert_property_in_org(db, caller, property_id)
    if body.action not in TRANSITIONS:
        raise HTTPException(status_code=422,
                            detail="Action must be approve, reject, pay or cancel.")
    leaves, target, needs = TRANSITIONS[body.action]
    if needs != "edit" and not _may(db, caller, property_id, needs):
        raise HTTPException(status_code=403,
                            detail="Approving or rejecting a voucher needs payments approval rights.")
    note = (body.note or "").strip() or None
    if body.action == "reject" and not note:
        raise HTTPException(status_code=422, detail="Say why the voucher is rejected.")

    row = db.execute(
        text("SELECT * FROM finance.expense_vouchers "
             "WHERE id = :i AND property_id = :p FOR UPDATE"),
        {"i": voucher_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Voucher not found.")
    if row["status"] not in leaves:
        raise HTTPException(
            status_code=409,
            detail=f"A voucher that is {STATUS_LABELS[row['status']].lower()} "
                   f"cannot be {target.replace('_', ' ')}.")

    deciding = body.action in ("approve", "reject")
    db.execute(
        text("""
            UPDATE finance.expense_vouchers SET
                status = :status,
                decided_by = CASE WHEN :deciding THEN CAST(:who AS uuid) ELSE decided_by END,
                decided_at = CASE WHEN :deciding THEN now() ELSE decided_at END,
                decision_note = COALESCE(CAST(:note AS text), decision_note),
                paid_at = CASE WHEN CAST(:status AS varchar) = 'paid' THEN now() ELSE paid_at END,
                updated_by = :who, updated_at = now(), version = version + 1
            WHERE id = :id
        """),
        {"id": voucher_id, "status": target, "deciding": deciding,
         "who": caller.user_id, "note": note},
    )
    record_audit(
        db, action=f"expense_voucher.{target}", entity_type="expense_voucher",
        entity_id=str(voucher_id), organization_id=row["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        before={"status": row["status"]}, after={"status": target},
        reason=note,
    )
    return _get(db, voucher_id, property_id)
