"""Payment Details and Reversal (screen 115).

Screen 114 corrects a *charge*. This one undoes a *payment*, and the two are
not the same problem. A wrong charge is the resort's mistake about what it is
owed; a wrong payment is money that has already changed hands, and putting it
back is a decision with a person's name on it.

Nothing here deletes anything. ``post_refund`` posts a debit against each of the
payment's allocations, so the folio balance rises back to where it was and both
the original payment and the reversal stay on the bill. The payment row is never
edited: a payment that was voided still says it was taken, next to the record of
it being given back.

**Three kinds, because they are three different events.**

*Void* — taken today, should not have been. Same business day, whole amount.
*Refund* — the guest is getting money back, possibly part of it.
*Reversal* — a settled payment recorded against the wrong folio or amount.

They share one ledger movement and differ in what policy allows. A void after
the business day has rolled is refused outright rather than quietly treated as
a refund, because the two mean different things to whoever reads the books
later.

**The request exists before the money moves, and often instead of it.** Over
the policy threshold it waits in the queue on screen 042 with the payment
untouched. ``refund_id`` stays null until the ledger actually runs.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.approvals import (
    approval_state,
    request_approval,
    resolve_policy,
    rule_text,
)
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

from .database import get_session
from .ledger import LedgerError, post_refund
from .receipt_pdf import receipt_number
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

reversal_router = APIRouter(tags=["payment-reversals"], route_class=TransactionalRoute)

def _open_shift_of(db: Session, caller: Caller, property_id) -> uuid.UUID | None:
    """The drawer this user currently has open at this property, if any.

    Resolved from the person doing the refund rather than passed in by the
    caller: the money leaves the till in front of them, and a client that can
    name any shift can charge a shortage to somebody else's drawer.

    The same helper exists in ``routes.py``; it is repeated rather than
    imported because these two modules are siblings and importing one route
    module into another to share ten lines of SQL is how an import cycle
    starts.
    """
    return db.execute(
        text("SELECT id FROM finance.cashier_shifts "
             "WHERE cashier_id = :u AND property_id = :p AND status = 'open'"),
        {"u": caller.user_id, "p": property_id},
    ).scalar()


KINDS = ("void", "refund", "reversal")
KIND_LABELS = {"void": "Void", "refund": "Refund", "reversal": "Reversal"}
# Which approval policy governs each kind. A void and a reversal are the same
# family of correction; a refund is money leaving and has its own rule.
KIND_CATEGORY = {"void": "reversal", "refund": "refund", "reversal": "reversal"}
# Only a refund may be for part of the payment. The other two undo the whole
# thing or they are not what they claim to be.
PARTIAL_ALLOWED = ("refund",)

REASONS = (
    "duplicate_payment", "wrong_amount", "wrong_folio", "guest_cancelled",
    "service_not_delivered", "overpayment", "deposit_returned",
    "card_declined_later", "goodwill", "other",
)
REASON_LABELS = {
    "duplicate_payment": "Duplicate payment",
    "wrong_amount": "Wrong amount taken",
    "wrong_folio": "Posted to the wrong folio",
    "guest_cancelled": "Guest cancelled",
    "service_not_delivered": "Service not delivered",
    "overpayment": "Overpayment",
    "deposit_returned": "Security deposit returned",
    "card_declined_later": "Card declined after capture",
    "goodwill": "Goodwill",
    "other": "Other",
}
METHOD_LABELS = {
    "cash": "Cash", "card": "Card", "upi": "UPI",
    "bank_transfer": "Bank Transfer", "cheque": "Cheque", "wallet": "Wallet",
}
SOURCE_LABELS = {
    "front_desk": "Front Desk", "deposit": "Deposit", "checkout": "Checkout",
    "pos": "Restaurant & POS", "night_audit": "Night Audit",
}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class TimelineRow(BaseModel):
    at: datetime
    title: str
    detail: str
    actor: str
    tone: str


class ReversalRow(BaseModel):
    id: uuid.UUID
    kind: str
    kind_label: str
    status: str
    amount: Decimal
    reason: str
    reason_label: str
    remarks: str
    approval_required: bool
    approval_status: str | None
    policy_rule_text: str | None
    created_by_name: str | None
    created_at: datetime
    posted_at: datetime | None
    posted_by_name: str | None
    decided_by: str | None
    decided_at: datetime | None
    decision_comment: str | None
    can_post: bool
    can_cancel: bool


class FolioItem(BaseModel):
    entry_id: uuid.UUID
    business_date: date
    posted_at: datetime
    description: str
    entry_type: str
    amount: Decimal
    #: What the folio stood at immediately after this entry.
    #:
    #: Accumulated oldest-first regardless of the order the rows come back in,
    #: which is why it is a window function with its own ORDER BY rather than
    #: something the caller can total up: the list is served newest-first, and
    #: a balance summed in that order would be every row's figure but the
    #: right one. Positive is owed by the guest, negative is held for them --
    #: the same sign the folio ledger uses.
    running: Decimal
    is_this_payment: bool
    reverses_something: bool


class ReconLine(BaseModel):
    label: str
    amount: Decimal
    note: str | None = None
    emphasis: bool = False


class Reconciliation(BaseModel):
    lines: list[ReconLine]
    balanced: bool
    verdict: str


class AuditRow(BaseModel):
    at: datetime
    action: str
    label: str
    actor: str
    reason: str | None
    detail: str | None


class RuleRow(BaseModel):
    kind: str
    label: str
    text: str
    short: str
    allowed: bool
    blocked_reason: str | None
    partial_allowed: bool


class PaymentBlock(BaseModel):
    payment_id: uuid.UUID
    #: The number printed on this payment's receipt. Served rather than built
    #: in the browser, which used to compose its own and got a different answer
    #: from the PDF the guest was holding.
    receipt_no: str
    received_at: datetime
    business_date: date | None
    guest_name: str | None
    reservation_number: str | None
    reservation_id: uuid.UUID | None
    room_code: str | None
    room_type: str | None
    arrival_date: date | None
    departure_date: date | None
    folio_id: uuid.UUID | None
    folio_no: str | None
    method: str
    method_label: str
    source: str
    source_label: str
    reference: str | None
    provider_transaction_id: str | None
    cashier: str | None
    notes: str | None
    payment_status: str
    settled_at: datetime | None
    amount: Decimal
    currency: str
    # What is left to give back after anything already returned.
    refunded: Decimal
    refundable: Decimal
    folio_balance: Decimal | None
    same_business_day: bool


class Context(BaseModel):
    payment: PaymentBlock
    timeline: list[TimelineRow]
    folio_items: list[FolioItem]
    reconciliation: Reconciliation
    audit: list[AuditRow]
    reversals: list[ReversalRow]
    rules: list[RuleRow]
    reasons: list[dict]
    can_create: bool
    can_post: bool


class ReversalIn(BaseModel):
    kind: str
    amount: Decimal | None = None  # defaults to the whole refundable balance
    reason: str
    remarks: str = Field(min_length=5, max_length=1000)


class Decided(BaseModel):
    id: uuid.UUID
    status: str
    approval_required: bool
    policy_rule_text: str
    folio_balance: Decimal | None
    message: str


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
    return db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "payments", "act": action,
         "prop": property_id},
    ).first() is not None


def _today(db: Session, property_id: uuid.UUID) -> date:
    return db.execute(
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
    ).scalar() or db.execute(text("SELECT CURRENT_DATE")).scalar_one()


_PAYMENT_SQL = """
    SELECT p.id, p.received_at, lower(p.method) AS method, p.reference,
           p.receipt_no,
           p.amount, p.currency, p.status, p.source, p.notes,
           p.provider_transaction_id,
           u.display_name AS cashier,
           fa.folio_id, f.folio_no, f.currency AS folio_currency,
           r.id AS reservation_id, r.number AS reservation_number,
           g.full_name AS guest_name, rm.code AS room_code,
           rm.room_type_name, rm.arrival_date, rm.departure_date,
           e.business_date, e.settled_at
    FROM finance.payments p
    LEFT JOIN iam.users u ON u.id = p.cashier_id
    LEFT JOIN LATERAL (
        SELECT pa.folio_id FROM finance.payment_allocations pa
        WHERE pa.payment_id = p.id ORDER BY pa.created_at LIMIT 1
    ) fa ON TRUE
    LEFT JOIN finance.folios f ON f.id = fa.folio_id
    LEFT JOIN LATERAL (
        SELECT x.business_date, x.posted_at AS settled_at
        FROM finance.folio_entries x
        WHERE x.source_id = p.id::text AND x.entry_type = 'credit'
        ORDER BY x.posted_at LIMIT 1
    ) e ON TRUE
    LEFT JOIN booking.reservations r ON r.id = f.reservation_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    LEFT JOIN LATERAL (
        SELECT rm2.code, rt.name AS room_type_name,
               ru.arrival_date, ru.departure_date
        FROM booking.reservation_units ru
        LEFT JOIN property.rooms rm2 ON rm2.id = ru.assigned_room_id
        LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
        WHERE ru.reservation_id = r.id ORDER BY ru.created_at LIMIT 1
    ) rm ON TRUE
    WHERE p.id = :pid AND p.property_id = :prop
"""


def _payment(db: Session, payment_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text(_PAYMENT_SQL), {"pid": payment_id, "prop": property_id}
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return row


def _refunded(db: Session, payment_id: uuid.UUID) -> Decimal:
    """What the ledger already gave back, on its own terms."""
    return db.execute(
        text("SELECT COALESCE(sum(amount), 0) FROM finance.refunds "
             "WHERE payment_id = :p AND status IN ('succeeded', 'pending')"),
        {"p": payment_id},
    ).scalar_one()


def _folio_balance(db: Session, folio_id) -> Decimal | None:
    if folio_id is None:
        return None
    return db.execute(
        text("SELECT COALESCE(sum(CASE WHEN entry_type = 'debit' THEN amount "
             "ELSE -amount END), 0) FROM finance.folio_entries WHERE folio_id = :f"),
        {"f": folio_id},
    ).scalar_one()


# --------------------------------------------------------------------------
# The screen
# --------------------------------------------------------------------------

def _approval_sentence(roles) -> str:
    """"Sent for approval by X" — or, when nobody is named, without the X.

    No policy is configured for every category, and one that names no approver
    roles produced "Sent for approval by ." on screen: a sentence with a hole
    in it, which reads as a bug rather than as the true statement that the
    request is going to a person rather than a rule.
    """
    named = " or ".join(r for r in (roles or []) if r)
    return (f"Sent for approval by {named}."
            if named else "Sent for approval.")

@reversal_router.get("/payments/{payment_id}/reversal-context", response_model=Context)
def context(
    payment_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """One payment, what has already been given back, and what may be now."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    p = _payment(db, payment_id, property_id)

    refunded = _refunded(db, payment_id)
    refundable = p["amount"] - refunded
    today = _today(db, property_id)
    same_day = p["business_date"] == today if p["business_date"] else False

    open_request = db.execute(
        text("SELECT kind FROM finance.payment_reversals WHERE payment_id = :p "
             "AND status IN ('pending_approval', 'approved')"),
        {"p": payment_id},
    ).scalar()

    rules: list[RuleRow] = []
    for kind in KINDS:
        policy = resolve_policy(db, prop["organization_id"], KIND_CATEGORY[kind])
        blocked = None
        if p["status"] != "succeeded":
            blocked = f"This payment is {p['status']}, so there is nothing to undo."
        elif refundable <= 0:
            blocked = "The whole payment has already been given back."
        elif open_request is not None:
            blocked = (f"A {KIND_LABELS[open_request].lower()} is already open on "
                       f"this payment.")
        elif kind == "void" and not same_day:
            blocked = ("A void is only for the day the payment was taken. "
                       "Use a refund or a reversal instead.")
        # One line, as the mockup writes it: who may approve, above what.
        if policy is None:
            short = "No policy — manual decision"
        elif policy["threshold_value"] is None:
            short = f"Always {' or '.join(policy['approver_roles'] or ['a manager'])}"
        else:
            who = " or ".join(policy["approver_roles"] or ["a manager"])
            short = f"Above Rs {policy['threshold_value']:,.0f} — {who}"
        rules.append(RuleRow(
            kind=kind, label=KIND_LABELS[kind], text=rule_text(policy),
            short=short,
            allowed=blocked is None, blocked_reason=blocked,
            partial_allowed=kind in PARTIAL_ALLOWED,
        ))

    return Context(
        payment=PaymentBlock(
            payment_id=p["id"], receipt_no=receipt_number(p["id"], p["receipt_no"]),
            received_at=p["received_at"],
            business_date=p["business_date"],
            guest_name=p["guest_name"],
            reservation_number=p["reservation_number"],
            reservation_id=p["reservation_id"], room_code=p["room_code"],
            room_type=p["room_type_name"],
            arrival_date=p["arrival_date"], departure_date=p["departure_date"],
            folio_id=p["folio_id"],
            folio_no=p["folio_no"],
            method=p["method"],
            method_label=METHOD_LABELS.get(p["method"], p["method"]),
            source=p["source"],
            source_label=SOURCE_LABELS.get(p["source"], p["source"]),
            reference=p["reference"],
            provider_transaction_id=p["provider_transaction_id"],
            cashier=p["cashier"], notes=p["notes"], payment_status=p["status"],
            settled_at=p["settled_at"],
            amount=p["amount"],
            currency=p["currency"] or prop["currency"] or "INR",
            refunded=refunded, refundable=refundable,
            folio_balance=_folio_balance(db, p["folio_id"]),
            same_business_day=same_day,
        ),
        timeline=_timeline(db, p),
        folio_items=_folio_items(db, p),
        reconciliation=_reconcile(db, p, refunded),
        audit=_audit(db, payment_id),
        reversals=_reversals(db, payment_id, caller, property_id),
        rules=rules,
        reasons=[{"value": r, "label": REASON_LABELS[r]} for r in REASONS],
        can_create=_may(db, caller, property_id, "create"),
        can_post=_may(db, caller, property_id, "cancel"),
    )


def _timeline(db: Session, p) -> list[TimelineRow]:
    """What actually happened to this payment, in order.

    Built from the ledger and the reversal record rather than a narrative
    table, so it cannot claim an event that left no trace. The mockup shows a
    gateway settlement and an emailed receipt; neither exists here — there is
    no real payment gateway and no mail transport — so neither is invented.
    """
    rows: list[TimelineRow] = []
    rows.append(TimelineRow(
        at=p["received_at"],
        title=f"{METHOD_LABELS.get(p['method'], p['method'])} payment taken",
        detail=f"{p['amount']:,.2f} recorded"
               + (f" · ref {p['reference']}" if p["reference"] else "")
               + (f" · by {p['cashier']}" if p["cashier"] else ""),
        actor=p["cashier"] or "System", tone="emerald",
    ))
    for e in db.execute(
        text("SELECT posted_at, entry_type, amount, business_date, source_type "
             "FROM finance.folio_entries WHERE source_id = :s ORDER BY posted_at"),
        {"s": str(p["id"])},
    ).mappings():
        credit = e["entry_type"] == "credit"
        rows.append(TimelineRow(
            at=e["posted_at"],
            title="Credited to the folio" if credit else "Reversed off the folio",
            detail=f"{e['amount']:,.2f} on business date {e['business_date']}"
                   + (f" as {e['source_type']}" if e["source_type"] else ""),
            actor="Ledger", tone="sky" if credit else "amber",
        ))
    for r in db.execute(
        text("SELECT rv.created_at, rv.posted_at, rv.kind, rv.amount, rv.status, "
             "       u.display_name AS who "
             "FROM finance.payment_reversals rv "
             "LEFT JOIN iam.users u ON u.id = rv.created_by "
             "WHERE rv.payment_id = :p ORDER BY rv.created_at"),
        {"p": p["id"]},
    ).mappings():
        rows.append(TimelineRow(
            at=r["created_at"],
            title=f"{KIND_LABELS.get(r['kind'], r['kind'])} requested",
            detail=f"{r['amount']:,.2f} · {r['status'].replace('_', ' ')}",
            actor=r["who"] or "Someone", tone="amber",
        ))
        if r["posted_at"]:
            rows.append(TimelineRow(
                at=r["posted_at"],
                title=f"{KIND_LABELS.get(r['kind'], r['kind'])} posted",
                detail=f"{r['amount']:,.2f} returned against this payment",
                actor=r["who"] or "Someone", tone="rose",
            ))
    return sorted(rows, key=lambda x: x.at)


def _describe_entry(source_type: str | None) -> str:
    return {
        "room_stay": "Room charge", "room_stay_tax": "Room tax",
        "restaurant": "Restaurant", "minibar": "Mini bar",
        "breakfast": "Breakfast", "spa": "Spa treatment",
        "laundry": "Laundry", "activity": "Activity",
        "security_deposit": "Security deposit", "payment": "Payment",
        "refund": "Refund", "adjustment": "Adjustment",
        "cancellation_fee": "Cancellation fee",
        "no_show_penalty": "No-show penalty",
        "no_show_penalty_tax": "No-show penalty tax",
        "reservation_change": "Reservation change", "room_move": "Room move",
    }.get(source_type or "", (source_type or "Entry").replace("_", " ").capitalize())


def _folio_items(db: Session, p) -> list[FolioItem]:
    """Every line on the folio this payment landed on.

    The whole folio rather than just this payment's own entries, because the
    person deciding whether to give money back should see what the guest was
    actually charged. This payment's own lines are marked so they stay findable
    in a long bill.
    """
    if p["folio_id"] is None:
        return []
    rows = db.execute(
        text(
            """
            SELECT id, business_date, posted_at, entry_type, amount,
                   source_type, source_id, reversal_of_id,
                   sum(CASE WHEN entry_type = 'debit' THEN amount
                            ELSE -amount END)
                     OVER (ORDER BY posted_at, id) AS running
            FROM finance.folio_entries
            WHERE folio_id = :f
            -- Newest first, and by posted_at rather than business_date: the
            -- two differ (a charge typed in September can carry December's
            -- business date, the night of the stay it belongs to), so sorting
            -- by one while the column prints the other makes a sorted list
            -- look shuffled.
            ORDER BY posted_at DESC, id DESC
            """
        ),
        {"f": p["folio_id"]},
    ).mappings().all()
    return [
        FolioItem(
            entry_id=r["id"], business_date=r["business_date"],
            posted_at=r["posted_at"],
            description=_describe_entry(r["source_type"]),
            entry_type=r["entry_type"], amount=r["amount"],
            running=r["running"],
            is_this_payment=r["source_id"] == str(p["id"]),
            reverses_something=r["reversal_of_id"] is not None,
        )
        for r in rows
    ]


def _reconcile(db: Session, p, refunded: Decimal) -> Reconciliation:
    """Does this payment add up?

    Three numbers have to agree: what the payment says was taken, what was
    allocated to folios, and what the ledger actually credited. They are all
    written by the same function and should never differ — which is exactly why
    it is worth showing. A mismatch means something wrote to the ledger outside
    the payment path, and that is worth finding before it compounds.
    """
    allocated = db.execute(
        text("SELECT COALESCE(sum(amount), 0) FROM finance.payment_allocations "
             "WHERE payment_id = :p"),
        {"p": p["id"]},
    ).scalar_one()
    credited = db.execute(
        text("SELECT COALESCE(sum(amount), 0) FROM finance.folio_entries "
             "WHERE source_id = :s AND entry_type = 'credit'"),
        {"s": str(p["id"])},
    ).scalar_one()
    # A refund's debit entries carry the REFUND's id, not the payment's — the
    # ledger writes them under the refund it created. Reaching for them by
    # payment id finds nothing and makes a healthy payment look unbalanced.
    debited = db.execute(
        text(
            """
            SELECT COALESCE(sum(e.amount), 0)
            FROM finance.folio_entries e
            JOIN finance.refunds rf ON rf.id::text = e.source_id
            WHERE rf.payment_id = :p AND e.entry_type = 'debit'
            """
        ),
        {"p": p["id"]},
    ).scalar_one()
    n_refunds = db.execute(
        text("SELECT count(*) FROM finance.refunds "
             "WHERE payment_id = :p AND status IN ('succeeded', 'pending')"),
        {"p": p["id"]},
    ).scalar_one()

    taken = p["amount"]
    method = METHOD_LABELS.get(p["method"], p["method"])
    ref_note = f" - ref {p['reference']}" if p["reference"] else ""
    lines = [
        ReconLine(label="Payment recorded", amount=taken,
                  note=f"{method}{ref_note}"),
        ReconLine(label="Allocated to folios", amount=allocated,
                  note="One allocation per folio the payment settled"),
        ReconLine(label="Credited on the ledger", amount=credited,
                  note="Credit entries carrying this payment's id"),
    ]
    if refunded > 0:
        plural = "" if n_refunds == 1 else "s"
        lines.append(ReconLine(
            label="Given back", amount=refunded,
            note=f"{n_refunds} refund row{plural}, "
                 f"{debited:,.2f} debited back to the folio"))
    lines.append(ReconLine(
        label="Net still held", amount=taken - refunded, emphasis=True,
        note="What the resort is still holding from this payment"))

    balanced = taken == allocated == credited and debited == refunded
    if balanced:
        verdict = ("This payment ties out: what was taken, what was allocated "
                   "and what the ledger credited all agree.")
    else:
        parts = []
        if taken != allocated:
            parts.append(f"recorded {taken:,.2f} but allocated {allocated:,.2f}")
        if allocated != credited:
            parts.append(f"allocated {allocated:,.2f} but credited {credited:,.2f}")
        if debited != refunded:
            parts.append(f"refunds total {refunded:,.2f} but {debited:,.2f} "
                         f"was debited back")
        verdict = ("This payment does not tie out - " + "; ".join(parts)
                   + ". Something wrote to the ledger outside the payment path.")
    return Reconciliation(lines=lines, balanced=balanced, verdict=verdict)


AUDIT_LABELS = {
    "payment.collected": "Payment taken",
    "payment.void.requested": "Void requested",
    "payment.void.posted": "Void posted",
    "payment.void.cancelled": "Void withdrawn",
    "payment.refund.requested": "Refund requested",
    "payment.refund.posted": "Refund posted",
    "payment.refund.cancelled": "Refund withdrawn",
    "payment.reversal.requested": "Reversal requested",
    "payment.reversal.posted": "Reversal posted",
    "payment.reversal.cancelled": "Reversal withdrawn",
}


def _audit(db: Session, payment_id: uuid.UUID) -> list[AuditRow]:
    """The append-only record, newest first.

    Read straight from ``iam.audit_events`` rather than reconstructed, so it
    shows what was actually written - including anything this screen did not
    do itself.
    """
    rows = db.execute(
        text(
            """
            SELECT a.occurred_at, a.action, a.actor_subject, a.reason,
                   a.redacted_after, u.display_name
            FROM iam.audit_events a
            LEFT JOIN iam.users u ON u.subject_id = a.actor_subject
            WHERE a.entity_type = 'payment' AND a.entity_id = :id
            ORDER BY a.occurred_at DESC
            """
        ),
        {"id": str(payment_id)},
    ).mappings().all()
    out = []
    for r in rows:
        after = r["redacted_after"] or {}
        bits = []
        if isinstance(after, dict):
            if after.get("amount"):
                bits.append(str(after["amount"]))
            if after.get("reason"):
                bits.append(str(after["reason"]).replace("_", " "))
            for key in ("folio_balance_after", "balance_after"):
                if after.get(key):
                    bits.append(f"balance after {after[key]}")
                    break
        fallback = r["action"].replace(".", " ").replace("_", " ")
        out.append(AuditRow(
            at=r["occurred_at"], action=r["action"],
            label=AUDIT_LABELS.get(r["action"], fallback),
            actor=r["display_name"] or r["actor_subject"] or "System",
            reason=r["reason"], detail=", ".join(bits) or None,
        ))
    return out


def _reversals(db: Session, payment_id, caller, property_id) -> list[ReversalRow]:
    rows = db.execute(
        text(
            """
            SELECT rv.*, cu.display_name AS created_by_name,
                   pu.display_name AS posted_by_name
            FROM finance.payment_reversals rv
            LEFT JOIN iam.users cu ON cu.id = rv.created_by
            LEFT JOIN iam.users pu ON pu.id = rv.posted_by
            WHERE rv.payment_id = :p
            ORDER BY rv.created_at DESC
            """
        ),
        {"p": payment_id},
    ).mappings().all()
    can_post = _may(db, caller, property_id, "cancel")
    out = []
    for r in rows:
        state = approval_state(db, r["approval_request_id"]) \
            if r["approval_request_id"] else None
        cleared = (not r["approval_required"]) or (
            state is not None and state["status"] == "approved")
        out.append(ReversalRow(
            id=r["id"], kind=r["kind"],
            kind_label=KIND_LABELS.get(r["kind"], r["kind"]),
            status=r["status"], amount=r["amount"], reason=r["reason"],
            reason_label=REASON_LABELS.get(r["reason"], r["reason"]),
            remarks=r["remarks"], approval_required=r["approval_required"],
            approval_status=state["status"] if state else None,
            policy_rule_text=r["policy_rule_text"],
            created_by_name=r["created_by_name"], created_at=r["created_at"],
            posted_at=r["posted_at"], posted_by_name=r["posted_by_name"],
            decided_by=state["decided_by"] if state else None,
            decided_at=state["decided_at"] if state else None,
            decision_comment=state["decision_comment"] if state else None,
            can_post=can_post and cleared and r["status"] in ("pending_approval",
                                                              "approved"),
            can_cancel=r["status"] in ("pending_approval", "approved"),
        ))
    return out


# --------------------------------------------------------------------------
# Asking
# --------------------------------------------------------------------------
@reversal_router.post(
    "/payments/{payment_id}/reversals", response_model=Decided,
    status_code=status.HTTP_201_CREATED,
)
def create_reversal(
    payment_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ReversalIn,
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Raise a void, refund or reversal. No money moves here."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    p = _payment(db, payment_id, property_id)

    if body.kind not in KINDS:
        raise HTTPException(status_code=422, detail="Unknown action type.")
    if body.reason not in REASONS:
        raise HTTPException(status_code=422, detail="Unknown reason.")
    if p["status"] != "succeeded":
        raise HTTPException(
            status_code=409,
            detail=f"This payment is {p['status']}, so there is nothing to undo.",
        )

    refundable = p["amount"] - _refunded(db, payment_id)
    if refundable <= 0:
        raise HTTPException(
            status_code=409,
            detail="The whole payment has already been given back.",
        )

    # Cash needs a drawer, and the request is the place to say so.
    #
    # The ledger refuses this too, but it refuses at the moment the money
    # actually moves — which for anything needing approval is after a manager
    # has looked at it. A cashier could raise a cash refund with no drawer
    # open, wait for approval, and only then be told it was never possible.
    # Asking here costs one query and turns that into an answer they get
    # while they are still standing at the desk.
    if (p["method"] or "").lower() == "cash" and not _open_shift_of(
        db, caller, property_id
    ):
        raise HTTPException(
            status_code=409,
            detail="A cash refund has to come out of an open drawer, and you "
                   "do not have one open. Open a cashier shift first, then "
                   "raise this refund.",
        )

    amount = body.amount if body.amount is not None else refundable
    if amount <= 0:
        raise HTTPException(status_code=422, detail="The amount must be positive.")
    if amount > refundable:
        raise HTTPException(
            status_code=422,
            detail=f"Only {refundable:,.2f} of this payment is left to give back.",
        )
    if body.kind not in PARTIAL_ALLOWED and amount != refundable:
        raise HTTPException(
            status_code=422,
            detail=f"A {KIND_LABELS[body.kind].lower()} takes the whole "
                   f"{refundable:,.2f}. Use a refund to give back part of it.",
        )
    if body.kind == "void":
        today = _today(db, property_id)
        if p["business_date"] != today:
            raise HTTPException(
                status_code=409,
                detail="A void is only for the day the payment was taken. Use a "
                       "refund or a reversal instead.",
            )

    open_now = db.execute(
        text("SELECT kind FROM finance.payment_reversals WHERE payment_id = :p "
             "AND status IN ('pending_approval', 'approved')"),
        {"p": payment_id},
    ).scalar()
    if open_now is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A {KIND_LABELS[open_now].lower()} is already open on this "
                   f"payment. Post or cancel it before raising another.",
        )

    outcome = request_approval(
        db,
        organization_id=prop["organization_id"],
        category=KIND_CATEGORY[body.kind],
        title=f"{KIND_LABELS[body.kind]} of Rs {amount:,.2f} on a payment",
        actor_subject=caller.subject, actor_user_id=caller.user_id,
        amount=amount, amount_context=REASON_LABELS[body.reason],
        entity_ref=f"PAY/{str(payment_id)[:8].upper()}",
        guest_name=p["guest_name"], property_id=property_id,
    )

    rev_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.payment_reversals
                (id, organization_id, property_id, payment_id, kind, status,
                 amount, currency, reason, remarks, approval_request_id,
                 approval_required, policy_rule_text, created_by)
            VALUES (:id, :org, :prop, :pay, :kind,
                    CASE WHEN :req THEN 'pending_approval' ELSE 'approved' END,
                    :amt, :cur, :reason, :remarks, :ar, :req, :rule, :by)
            """
        ),
        {"id": rev_id, "org": prop["organization_id"], "prop": property_id,
         "pay": payment_id, "kind": body.kind, "req": outcome.required,
         "amt": amount, "cur": p["currency"] or prop["currency"] or "INR",
         "reason": body.reason, "remarks": body.remarks,
         "ar": outcome.request_id, "rule": outcome.rule_text,
         "by": caller.user_id},
    )
    record_audit(
        db, action=f"payment.{body.kind}.requested", entity_type="payment",
        entity_id=str(payment_id), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.remarks,
        after={"reversal_id": str(rev_id), "kind": body.kind,
               "amount": str(amount), "reason": body.reason,
               "approval_required": outcome.required,
               "approval_request_id": str(outcome.request_id)},
    )
    return Decided(
        id=rev_id,
        status="pending_approval" if outcome.required else "approved",
        approval_required=outcome.required,
        policy_rule_text=outcome.rule_text,
        folio_balance=_folio_balance(db, p["folio_id"]),
        message=(
            _approval_sentence(outcome.approver_roles)
            + " The payment is untouched until they decide."
            if outcome.required else
            f"Cleared under policy and ready to post. Nothing has been given "
            f"back yet."
        ),
    )


# --------------------------------------------------------------------------
# Doing it — the only place money goes back
# --------------------------------------------------------------------------
@reversal_router.post("/payment-reversals/{reversal_id}/post", response_model=Decided)
def post_reversal(
    reversal_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "cancel")),
    db: Session = Depends(get_session),
):
    """Run the ledger. Refused unless the approval actually came through."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    rv = db.execute(
        text("SELECT * FROM finance.payment_reversals "
             "WHERE id = :id AND property_id = :p"),
        {"id": reversal_id, "p": property_id},
    ).mappings().first()
    if rv is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if rv["status"] == "posted":
        raise HTTPException(status_code=409, detail="That request is already posted.")
    if rv["status"] in ("rejected", "cancelled"):
        raise HTTPException(
            status_code=409, detail=f"That request was {rv['status']}.")

    if rv["approval_required"]:
        state = approval_state(db, rv["approval_request_id"])
        where = state["status"] if state else "missing"
        if where == "rejected":
            db.execute(
                text("UPDATE finance.payment_reversals SET status = 'rejected', "
                     "updated_at = now(), version = version + 1 WHERE id = :id"),
                {"id": reversal_id},
            )
            raise HTTPException(
                status_code=409,
                detail="That request was rejected, so nothing can be given back.",
            )
        if where != "approved":
            raise HTTPException(
                status_code=409,
                detail=f"This request is still {where} approval. "
                       f"{rv['policy_rule_text']}",
            )

    try:
        res = post_refund(
            db,
            organization_id=rv["organization_id"],
            property_id=property_id,
            payment_id=rv["payment_id"],
            amount=rv["amount"],
            business_date=_today(db, property_id),
            reason=f"{KIND_LABELS[rv['kind']]}: {rv['remarks']}"[:200],
            # What the folio line calls itself. The act, then why -- "Void -
            # Overpayment" -- because a bill that says "Refund" against every
            # one of these cannot tell a guest, or an auditor six months on,
            # which of the three actually happened. The operator's own remarks
            # stay on the refund record and on the Reversals tab; the ledger
            # gets the short form it has room for.
            note=f"{KIND_LABELS[rv['kind']]} — {REASON_LABELS[rv['reason']]}",
            currency=rv["currency"],
            # Cash given back leaves this cashier's drawer, so the count has
            # to see it go.
            cashier_shift_id=_open_shift_of(db, caller, property_id),
        )
    except LedgerError as exc:
        raise HTTPException(
            status_code=409 if getattr(exc, "conflict", False) else 422,
            detail=str(exc),
        ) from exc

    db.execute(
        text("UPDATE finance.payment_reversals SET status = 'posted', "
             "refund_id = :r, posted_at = now(), posted_by = :u, "
             "updated_at = now(), version = version + 1 WHERE id = :id"),
        {"r": res.refund_id, "u": caller.user_id, "id": reversal_id},
    )

    p = _payment(db, rv["payment_id"], property_id)
    balance = _folio_balance(db, p["folio_id"])
    record_audit(
        db, action=f"payment.{rv['kind']}.posted", entity_type="payment",
        entity_id=str(rv["payment_id"]), organization_id=rv["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        before={"status": rv["status"]},
        after={"reversal_id": str(reversal_id), "refund_id": str(res.refund_id),
               "amount": str(rv["amount"]),
               "folio_balance_after": str(balance) if balance is not None else None},
    )
    return Decided(
        id=reversal_id, status="posted",
        approval_required=rv["approval_required"],
        policy_rule_text=rv["policy_rule_text"] or "",
        folio_balance=balance,
        message=f"{KIND_LABELS[rv['kind']]} of {rv['amount']:,.2f} posted."
                + (f" The folio balance is now {balance:,.2f}."
                   if balance is not None else ""),
    )


@reversal_router.post("/payment-reversals/{reversal_id}/cancel", response_model=Decided)
def cancel_reversal(
    reversal_id: uuid.UUID,
    property_id: uuid.UUID,
    remarks: str = Query(..., min_length=5, max_length=1000),
    caller: Caller = Depends(require_permission("payments", "cancel")),
    db: Session = Depends(get_session),
):
    """Withdraw a request before it is posted. Nothing was moved, so nothing
    is unwound — the record of having asked stays."""
    assert_property_in_org(db, caller, property_id)
    rv = db.execute(
        text("SELECT * FROM finance.payment_reversals "
             "WHERE id = :id AND property_id = :p"),
        {"id": reversal_id, "p": property_id},
    ).mappings().first()
    if rv is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if rv["status"] not in ("pending_approval", "approved"):
        raise HTTPException(
            status_code=409,
            detail=f"That request is {rv['status'].replace('_', ' ')} and cannot "
                   f"be withdrawn.",
        )
    db.execute(
        text("UPDATE finance.payment_reversals SET status = 'cancelled', "
             "remarks = remarks || ' | Withdrawn: ' || CAST(:r AS varchar), "
             "updated_at = now(), version = version + 1 WHERE id = :id"),
        {"r": remarks, "id": reversal_id},
    )
    p = _payment(db, rv["payment_id"], property_id)
    record_audit(
        db, action=f"payment.{rv['kind']}.cancelled", entity_type="payment",
        entity_id=str(rv["payment_id"]), organization_id=rv["organization_id"],
        property_id=property_id, actor_subject=caller.subject, reason=remarks,
        before={"status": rv["status"]}, after={"status": "cancelled"},
    )
    return Decided(
        id=reversal_id, status="cancelled",
        approval_required=rv["approval_required"],
        policy_rule_text=rv["policy_rule_text"] or "",
        folio_balance=_folio_balance(db, p["folio_id"]),
        message="Request withdrawn. The payment is untouched.",
    )
