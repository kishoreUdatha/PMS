"""Guest Folio Adjustment (screen 114).

A folio entry is never edited and never deleted. Taking a wrong charge off a
guest's bill means posting a *reversing* entry beside the original, so the bill
still shows what was charged, what was taken off, and by how much the two
differ. That is the whole design, and everything here follows from it.

Three things this screen is careful about.

**Nothing moves until it is allowed to.** An adjustment is created, routed
through ``chirala_common.approvals`` against the property's own policy, and
posted only once it is approved. Under the threshold the policy clears it
immediately and says so; over the threshold it waits in the queue on screen 042
and the folio balance does not move a rupee in the meantime. ``posted_entry_id``
is null until the credit actually exists, which is the only honest signal that
the folio has changed.

**Tax travels with the charge it belongs to.** Removing a 5,000 spa treatment
that carried 900 of tax means removing 5,900 — the tax was only ever owed
because the treatment was. The operator can decline that, and the record says
which they chose, because a tax return has to be able to tell the two apart.

**A charge can only be adjusted once at a time.** A partial index enforces it.
Two people crediting the same wrong line is how a guest ends up refunded twice
for one mistake, and it is the kind of thing that is only ever noticed later.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.approvals import approval_state, request_approval
from chirala_common import charge_types
from chirala_common.audit import record_audit
from chirala_common.authz import (
    _GRANT_SQL,
    Caller,
    assert_property_in_org,
    build_authz,
)
from chirala_common.objectstore import (
    ObjectStoreConfig,
    ObjectStoreError,
    build_key,
    delete_object,
    presigned_url,
    put_object,
)
from chirala_common.routing import TransactionalRoute
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .routes import _trading_day
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

adjustment_router = APIRouter(tags=["folio-adjustments"], route_class=TransactionalRoute)

_STORE = ObjectStoreConfig(
    endpoint=settings.minio_endpoint,
    public_endpoint=settings.minio_public_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    bucket=settings.minio_bucket,
    secure=settings.minio_secure,
)
ALLOWED_DOC_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_DOC_BYTES = 8 * 1024 * 1024

#: Debits that gave money BACK rather than charging it.
#:
#: They are debits, so every "is this a charge" test written as
#: ``entry_type = 'debit'`` swept them in: they were offered for adjustment on
#: a screen headed Posted Charges, and counted in Total charges. A folio whose
#: guest was charged 100 and refunded 23,000 read "Total charges 23,100", and
#: the adjustment form would happily raise a correcting CREDIT against a
#: refund -- money back on the folio with no payment, no receipt and nothing
#: at the gateway behind it.
REFUND_SOURCES = charge_types.REFUND_SOURCES

KINDS = ("correction", "discount", "allowance")
KIND_LABELS = {
    "correction": "Correction", "discount": "Discount", "allowance": "Allowance",
}
REASONS = (
    "incorrect_charge", "service_not_availed", "duplicate_posting",
    "price_correction", "guest_complaint", "goodwill", "billing_error", "other",
)
REASON_LABELS = {
    "incorrect_charge": "Incorrect charge posted",
    "service_not_availed": "Service not availed",
    "duplicate_posting": "Duplicate posting",
    "price_correction": "Price correction",
    "guest_complaint": "Guest complaint",
    "goodwill": "Goodwill gesture",
    "billing_error": "Billing error",
    "other": "Other",
}
# What each entry source_type is called on a bill a person reads.
DEPARTMENTS = {
    "room_stay": "Rooms", "room_stay_tax": "Rooms",
    "restaurant": "F&B", "minibar": "F&B", "breakfast": "F&B",
    "spa": "Spa", "activity": "Activities", "laundry": "Housekeeping",
    "transport": "Transport",
    "cancellation_fee": "Reservations", "no_show_penalty": "Reservations",
    "no_show_penalty_tax": "Reservations", "reservation_change": "Reservations",
    "room_move": "Rooms", "security_deposit": "Front Desk",
    "adjustment": "Adjustments",
}
# A guest who left long ago is a credit note, not a folio adjustment.
ADJUSTABLE_DAYS_AFTER_CHECKOUT = 30


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class ChargeRow(BaseModel):
    entry_id: uuid.UUID
    business_date: date
    #: When the line was actually posted. The business date alone cannot tell
    #: three charges on one day apart, and this table is where somebody picks
    #: WHICH of them to credit back -- "18 Sep 2026" against "18 Sep 2026" is
    #: not a choice. The folio ledger has shown the time all along; this list
    #: ordered by it and then declined to print it.
    posted_at: datetime
    description: str
    department: str
    source_type: str
    amount: Decimal
    tax_amount: Decimal
    total: Decimal
    entry_status: str
    adjustable: bool
    blocked_reason: str | None


class FolioSummary(BaseModel):
    folio_id: uuid.UUID
    folio_no: str
    folio_type: str
    status: str
    currency: str
    total_charges: Decimal
    total_payments: Decimal
    total_adjustments: Decimal
    balance: Decimal


class GuestBlock(BaseModel):
    guest_name: str | None
    email: str | None
    phone: str | None
    reservation_number: str | None
    reservation_id: uuid.UUID | None
    arrival_date: date | None
    departure_date: date | None
    room_type: str | None
    room_code: str | None
    adults: int | None
    children: int | None
    reservation_status: str | None


class PolicyBlock(BaseModel):
    name: str | None
    rule_text: str
    threshold: Decimal | None
    approver_roles: list[str]
    days_after_checkout: int


class AdjustmentRow(BaseModel):
    id: uuid.UUID
    kind: str
    kind_label: str
    status: str
    amount: Decimal
    tax_amount: Decimal
    adjust_tax: bool
    reason: str
    reason_label: str
    remarks: str
    charge_description: str | None
    evidence_name: str | None
    evidence_url: str | None
    approval_required: bool
    approval_status: str | None
    policy_rule_text: str | None
    created_by_name: str | None
    created_at: datetime
    posted_at: datetime | None
    posted_by_name: str | None
    decided_at: datetime | None
    decided_by: str | None
    decision_comment: str | None
    can_post: bool
    can_reverse: bool


class Context(BaseModel):
    guest: GuestBlock
    folio: FolioSummary
    charges: list[ChargeRow]
    adjustments: list[AdjustmentRow]
    policy: PolicyBlock
    kinds: list[dict]
    reasons: list[dict]
    can_create: bool
    can_post: bool
    can_reverse: bool
    adjustable: bool
    not_adjustable_reason: str | None


class AdjustmentIn(BaseModel):
    folio_entry_id: uuid.UUID | None = None
    kind: str = "correction"
    amount: Decimal = Field(gt=0)
    adjust_tax: bool = True
    reason: str
    remarks: str = Field(min_length=5, max_length=1000)


class Decided(BaseModel):
    id: uuid.UUID
    status: str
    approval_required: bool
    policy_rule_text: str
    balance_after: Decimal
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


def _folio(db: Session, folio_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text("SELECT * FROM finance.folios WHERE id = :f AND property_id = :p"),
        {"f": folio_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Folio not found")
    return row


def _balance(db: Session, folio_id: uuid.UUID) -> Decimal:
    return db.execute(
        text("SELECT COALESCE(sum(CASE WHEN entry_type = 'debit' THEN amount "
             "ELSE -amount END), 0) FROM finance.folio_entries WHERE folio_id = :f"),
        {"f": folio_id},
    ).scalar_one()


def _describe(source_type: str | None) -> str:
    """A charge line in words. Falls back to the raw source rather than 'Item'."""
    return {
        "room_stay": "Room charge", "room_stay_tax": "Room tax",
        "restaurant": "Restaurant", "minibar": "Mini bar",
        "breakfast": "Breakfast", "spa": "Spa treatment",
        "laundry": "Laundry service", "activity": "Activity",
        "transport": "Transport", "security_deposit": "Security deposit",
        "cancellation_fee": "Cancellation fee",
        "no_show_penalty": "No-show penalty",
        "no_show_penalty_tax": "No-show penalty tax",
        "reservation_change": "Reservation change",
        "room_move": "Room move", "adjustment": "Adjustment",
        "payment": "Payment",
    }.get(source_type or "", (source_type or "Charge").replace("_", " ").capitalize())


def _evidence_url(key: str | None) -> str | None:
    if not key:
        return None
    try:
        return presigned_url(_STORE, key)
    except ObjectStoreError:
        return None


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

@adjustment_router.get("/folios/{folio_id}/adjustment-context", response_model=Context)
def context(
    folio_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Everything the adjustment screen shows for one folio."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    folio = _folio(db, folio_id, property_id)

    guest = db.execute(
        text(
            """
            SELECT r.id AS reservation_id, r.number, r.status AS res_status,
                   g.full_name, g.email, g.phone,
                   ru.arrival_date, ru.departure_date, ru.adults, ru.children,
                   rt.name AS room_type, rm.code AS room_code
            FROM finance.folios f
            LEFT JOIN booking.reservations r ON r.id = f.reservation_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            LEFT JOIN LATERAL (
                SELECT * FROM booking.reservation_units u
                WHERE u.reservation_id = r.id ORDER BY u.created_at LIMIT 1
            ) ru ON TRUE
            LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            WHERE f.id = :f
            """
        ),
        {"f": folio_id},
    ).mappings().first()

    totals = db.execute(
        text(
            """
            -- Adjustments are counted apart from payments. An allowance
            -- credit is money the resort gave up, not money the guest handed
            -- over, and a summary that calls it a payment tells the front desk
            -- the guest has paid more than they have.
            SELECT COALESCE(sum(amount) FILTER (
                       WHERE entry_type = 'debit'
                         AND COALESCE(source_type, '') <> 'adjustment'
                         AND COALESCE(source_type, '') <> ALL(:refunds)), 0) AS charges,
                   COALESCE(sum(amount) FILTER (
                       WHERE entry_type = 'credit'
                         AND COALESCE(source_type, '') <> 'adjustment'), 0) AS payments,
                   COALESCE(sum(CASE WHEN entry_type = 'credit' THEN amount
                                     ELSE -amount END) FILTER (
                       WHERE COALESCE(source_type, '') = 'adjustment'), 0)
                       AS adjustments,
                   -- Every entry, nothing excluded. The balance used to be
                   -- derived as charges - payments - adjustments, which was
                   -- right only for as long as `charges` wrongly counted
                   -- refunds: take the refunds out of that figure -- which is
                   -- the whole point of doing so -- and the balance silently
                   -- moved by the amount refunded. A folio owing 24,200 read
                   -- 47,200. Summing the entries owes nothing to what the
                   -- three figures above choose to include.
                   COALESCE(sum(CASE WHEN entry_type = 'debit' THEN amount
                                     ELSE -amount END), 0) AS balance
            FROM finance.folio_entries WHERE folio_id = :f
            """
        ),
        {"f": folio_id, "refunds": list(REFUND_SOURCES)},
    ).mappings().first()

    # Which charges may still be adjusted. A line already reversed, already
    # under adjustment, or itself an adjustment, may not be.
    rows = db.execute(
        text(
            """
            SELECT e.id, e.business_date, e.posted_at, e.source_type, e.amount,
                   COALESCE(t.tax, 0) AS tax_amount,
                   -- Tax already inside the charge must not be credited on
                   -- top of it. Exclusive tax was posted as its own debit and
                   -- does have to come back separately.
                   COALESCE(t.exclusive_tax, 0) AS exclusive_tax_amount,
                   rev.id IS NOT NULL AS reversed,
                   adj.id IS NOT NULL AS under_adjustment,
                   adj.status AS adj_status
            FROM finance.folio_entries e
            LEFT JOIN LATERAL (
                SELECT sum(tax_amount) AS tax,
                       sum(tax_amount) FILTER (
                           WHERE x.apply_as = 'exclusive') AS exclusive_tax
                  FROM finance.folio_entry_taxes x
                 WHERE x.folio_entry_id = e.id
            ) t ON TRUE
            -- A reversal posted by something other than an adjustment — a
            -- charge cancelled at source, say. An adjustment's own credit is
            -- excluded here because the adjustments table below tracks it, and
            -- an adjustment that was later reversed leaves the charge standing
            -- and adjustable again.
            LEFT JOIN finance.folio_entries rev
              ON rev.reversal_of_id = e.id
             AND COALESCE(rev.source_type, '') <> 'adjustment'
            LEFT JOIN finance.folio_adjustments adj
              ON adj.folio_entry_id = e.id
             AND adj.status IN ('pending_approval', 'approved', 'posted')
            WHERE e.folio_id = :f
              AND e.entry_type = 'debit'
              AND e.reversal_of_id IS NULL
              -- A refund is not a charge and cannot be adjusted. See
              -- REFUND_SOURCES.
              AND COALESCE(e.source_type, '') <> ALL(:refunds)
            -- Newest first, by the same stamp the column shows.
            --
            -- This is a picker, not a running account: there is no balance
            -- accumulating down the page, and the line somebody has come here
            -- to credit back is almost always the one just posted. The folio
            -- ledger reads oldest-first for the opposite reason and should
            -- stay that way.
            --
            -- Ordered by posted_at, not business_date. They are not the same
            -- and the difference is visible on this very folio: a spa charge
            -- typed on 17 September carries business date 24 December, the
            -- night of the stay it belongs to. Sorting by one while printing
            -- the other is how a sorted list looks shuffled.
            ORDER BY e.posted_at DESC
            """
        ),
        {"f": folio_id, "refunds": list(REFUND_SOURCES)},
    ).mappings().all()

    charges: list[ChargeRow] = []
    for r in rows:
        blocked = None
        if r["reversed"]:
            blocked = "Already reversed"
        elif r["under_adjustment"]:
            blocked = {
                "pending_approval": "An adjustment on this charge is waiting "
                                    "for approval",
                "approved": "An approved adjustment on this charge is not "
                            "posted yet",
                "posted": "Already adjusted",
            }.get(r["adj_status"], "An adjustment is open on this charge")
        elif r["source_type"] == "adjustment":
            blocked = "This line is itself an adjustment"
        charges.append(ChargeRow(
            entry_id=r["id"], business_date=r["business_date"],
            posted_at=r["posted_at"],
            description=_describe(r["source_type"]),
            department=DEPARTMENTS.get(r["source_type"] or "", "Other"),
            source_type=r["source_type"] or "",
            amount=r["amount"] - r["tax_amount"], tax_amount=r["tax_amount"],
            total=r["amount"], entry_status="Posted",
            adjustable=blocked is None, blocked_reason=blocked,
        ))

    # How long since the guest left. A folio months old is a credit note.
    not_adjustable = None
    if folio["status"] != "open":
        not_adjustable = "This folio is closed."
    elif guest and guest["departure_date"]:
        gone = (db.execute(text("SELECT CURRENT_DATE")).scalar_one()
                - guest["departure_date"]).days
        if gone > ADJUSTABLE_DAYS_AFTER_CHECKOUT:
            not_adjustable = (
                f"The guest checked out {gone} days ago. Adjustments are "
                f"allowed for {ADJUSTABLE_DAYS_AFTER_CHECKOUT} days after "
                f"checkout; beyond that this needs a credit note."
            )

    from chirala_common.approvals import resolve_policy, rule_text

    policy = resolve_policy(db, prop["organization_id"], "adjustment")

    return Context(
        guest=GuestBlock(
            guest_name=guest["full_name"] if guest else None,
            email=guest["email"] if guest else None,
            phone=guest["phone"] if guest else None,
            reservation_number=guest["number"] if guest else None,
            reservation_id=guest["reservation_id"] if guest else None,
            arrival_date=guest["arrival_date"] if guest else None,
            departure_date=guest["departure_date"] if guest else None,
            room_type=guest["room_type"] if guest else None,
            room_code=guest["room_code"] if guest else None,
            adults=guest["adults"] if guest else None,
            children=guest["children"] if guest else None,
            reservation_status=guest["res_status"] if guest else None,
        ),
        folio=FolioSummary(
            folio_id=folio["id"], folio_no=folio["folio_no"],
            folio_type=(folio["type"] or "guest").replace("_", " ").title(),
            status=folio["status"], currency=folio["currency"] or prop["currency"],
            total_charges=totals["charges"], total_payments=totals["payments"],
            total_adjustments=totals["adjustments"],
            balance=totals["balance"],
        ),
        charges=charges,
        adjustments=_adjustments(db, folio_id, caller, property_id),
        policy=PolicyBlock(
            name=policy["name"] if policy else None,
            rule_text=rule_text(policy),
            threshold=policy["threshold_value"] if policy else None,
            approver_roles=list(policy["approver_roles"] or []) if policy else [],
            days_after_checkout=ADJUSTABLE_DAYS_AFTER_CHECKOUT,
        ),
        kinds=[{"value": k, "label": KIND_LABELS[k]} for k in KINDS],
        reasons=[{"value": r, "label": REASON_LABELS[r]} for r in REASONS],
        can_create=_may(db, caller, property_id, "create"),
        can_post=_may(db, caller, property_id, "edit"),
        can_reverse=_may(db, caller, property_id, "cancel"),
        adjustable=not_adjustable is None,
        not_adjustable_reason=not_adjustable,
    )


def _adjustments(db: Session, folio_id, caller, property_id) -> list[AdjustmentRow]:
    """This folio's adjustments, with where each one got to."""
    rows = db.execute(
        text(
            """
            SELECT a.*, e.source_type AS charge_source,
                   cu.display_name AS created_by_name,
                   pu.display_name AS posted_by_name
            FROM finance.folio_adjustments a
            LEFT JOIN finance.folio_entries e ON e.id = a.folio_entry_id
            LEFT JOIN iam.users cu ON cu.id = a.created_by
            LEFT JOIN iam.users pu ON pu.id = a.posted_by
            WHERE a.folio_id = :f
            ORDER BY a.created_at DESC
            """
        ),
        {"f": folio_id},
    ).mappings().all()

    can_post = _may(db, caller, property_id, "edit")
    can_reverse = _may(db, caller, property_id, "cancel")
    out = []
    for r in rows:
        state = approval_state(db, r["approval_request_id"]) \
            if r["approval_request_id"] else None
        approved = (not r["approval_required"]) or (
            state is not None and state["status"] == "approved")
        out.append(AdjustmentRow(
            id=r["id"], kind=r["kind"], kind_label=KIND_LABELS.get(r["kind"], r["kind"]),
            status=r["status"], amount=r["amount"], tax_amount=r["tax_amount"],
            adjust_tax=r["adjust_tax"], reason=r["reason"],
            reason_label=REASON_LABELS.get(r["reason"], r["reason"]),
            remarks=r["remarks"],
            charge_description=_describe(r["charge_source"]) if r["charge_source"]
            else None,
            evidence_name=r["evidence_name"],
            evidence_url=_evidence_url(r["evidence_key"]),
            approval_required=r["approval_required"],
            approval_status=state["status"] if state else None,
            policy_rule_text=r["policy_rule_text"],
            created_by_name=r["created_by_name"], created_at=r["created_at"],
            posted_at=r["posted_at"], posted_by_name=r["posted_by_name"],
            decided_at=state["decided_at"] if state else None,
            decided_by=state["decided_by"] if state else None,
            decision_comment=state["decision_comment"] if state else None,
            can_post=can_post and approved and r["status"] in ("pending_approval",
                                                               "approved"),
            can_reverse=can_reverse and r["status"] == "posted",
        ))
    return out


# --------------------------------------------------------------------------
# Creating one
# --------------------------------------------------------------------------
@adjustment_router.post(
    "/folios/{folio_id}/adjustments", response_model=Decided,
    status_code=status.HTTP_201_CREATED,
)
def create_adjustment(
    folio_id: uuid.UUID,
    property_id: uuid.UUID,
    body: AdjustmentIn,
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Raise an adjustment and route it. Nothing is posted here."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    folio = _folio(db, folio_id, property_id)
    if folio["status"] != "open":
        raise HTTPException(
            status_code=409,
            detail="That folio is closed. A closed folio is corrected with a "
                   "credit note, not an adjustment.",
        )
    if body.kind not in KINDS:
        raise HTTPException(status_code=422, detail="Unknown adjustment type.")
    if body.reason not in REASONS:
        raise HTTPException(status_code=422, detail="Unknown reason.")

    tax_part = Decimal("0")
    entry = None
    if body.folio_entry_id is not None:
        entry = db.execute(
            text(
                """
                SELECT e.*, COALESCE(t.tax, 0) AS tax_amount,
                       -- Only the tax posted beside the charge, not the tax
                       -- already inside it. This is the figure the credit may
                       -- add; see where tax_part is worked out.
                       COALESCE(t.exclusive_tax, 0) AS exclusive_tax_amount,
                       rev.id IS NOT NULL AS reversed
                FROM finance.folio_entries e
                LEFT JOIN LATERAL (
                    SELECT sum(tax_amount) AS tax,
                           sum(tax_amount) FILTER (
                               WHERE x.apply_as = 'exclusive') AS exclusive_tax
                      FROM finance.folio_entry_taxes x
                     WHERE x.folio_entry_id = e.id
                ) t ON TRUE
                -- Same rule the context listing uses: an adjustment's own
                -- credit is not a reversal of the charge for this purpose,
                -- because an adjustment that was later reversed leaves the
                -- charge standing. Without the matching filter the list offers
                -- a charge the create path then refuses.
                LEFT JOIN finance.folio_entries rev
                  ON rev.reversal_of_id = e.id
                 AND COALESCE(rev.source_type, '') <> 'adjustment'
                WHERE e.id = :e AND e.folio_id = :f
                """
            ),
            {"e": body.folio_entry_id, "f": folio_id},
        ).mappings().first()
        if entry is None:
            raise HTTPException(status_code=404, detail="That charge is not on this folio.")
        if entry["entry_type"] != "debit":
            raise HTTPException(
                status_code=422,
                detail="Only a charge can be adjusted. To undo a payment, use "
                       "Payment Reversal.",
            )
        # A refund is a debit, so the test above let one through. Adjusting one
        # posts a credit against money that has already gone back to the guest
        # -- a credit on the folio with no payment, no receipt and nothing at
        # the gateway behind it. The listing no longer offers these; this is
        # the refusal for anything that asks anyway.
        if (entry["source_type"] or "") in REFUND_SOURCES:
            raise HTTPException(
                status_code=422,
                detail="That line is a refund, not a charge. Money already "
                       "given back cannot be adjusted -- use Payment Reversal "
                       "on the payment itself.",
            )
        if entry["reversed"]:
            raise HTTPException(status_code=409, detail="That charge is already reversed.")
        if body.amount > entry["amount"]:
            raise HTTPException(
                status_code=422,
                detail=f"An adjustment cannot exceed the charge. That line is "
                       f"{entry['amount']:,.2f}.",
            )
        # The tax share of what is being taken off, when the operator asked for
        # tax to follow the charge.
        # Only the tax that was added ON TOP of the charge comes back as a
        # separate credit. Inclusive tax is already inside ``body.amount``, so
        # crediting it again returned 1,100 against a 1,050 charge -- the
        # guest 50 up, and a folio that reconciles to nothing.
        if body.adjust_tax and entry["exclusive_tax_amount"] and entry["amount"]:
            tax_part = (
                entry["exclusive_tax_amount"] * body.amount / entry["amount"]
            ).quantize(Decimal("0.01"))

        standing = db.execute(
            text("SELECT status FROM finance.folio_adjustments "
                 "WHERE folio_entry_id = :e "
                 "AND status IN ('pending_approval', 'approved', 'posted') "
                 "ORDER BY created_at DESC LIMIT 1"),
            {"e": body.folio_entry_id},
        ).scalar()
        if standing is not None:
            raise HTTPException(
                status_code=409,
                detail="That charge has already been adjusted."
                if standing == "posted" else
                "There is already an open adjustment on that charge. Post or "
                "cancel it before raising another.",
            )

    who = db.execute(
        text("SELECT g.full_name AS guest_name, f.folio_no "
             "FROM finance.folios f "
             "LEFT JOIN booking.reservations r ON r.id = f.reservation_id "
             "LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id "
             "WHERE f.id = :f"),
        {"f": folio_id},
    ).mappings().one()
    guest_name = who["guest_name"]

    outcome = request_approval(
        db,
        organization_id=prop["organization_id"],
        category="adjustment",
        title=f"{KIND_LABELS[body.kind]} of Rs {body.amount:,.2f} on a guest folio",
        actor_subject=caller.subject, actor_user_id=caller.user_id,
        amount=body.amount,
        amount_context=REASON_LABELS[body.reason],
        entity_ref=who["folio_no"],
        guest_name=guest_name, property_id=property_id,
    )

    adj_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folio_adjustments
                (id, organization_id, property_id, folio_id, folio_entry_id,
                 kind, status, amount, tax_amount, adjust_tax, currency,
                 reason, remarks, approval_request_id, approval_required,
                 policy_rule_text, created_by)
            VALUES (:id, :org, :prop, :f, :e, :kind,
                    CASE WHEN :req THEN 'pending_approval' ELSE 'approved' END,
                    :amt, :tax, :atax, :cur, :reason, :remarks, :ar, :req,
                    :rule, :by)
            """
        ),
        {
            "id": adj_id, "org": prop["organization_id"], "prop": property_id,
            "f": folio_id, "e": body.folio_entry_id, "kind": body.kind,
            "req": outcome.required, "amt": body.amount, "tax": tax_part,
            "atax": body.adjust_tax,
            "cur": folio["currency"] or prop["currency"] or "INR",
            "reason": body.reason, "remarks": body.remarks,
            "ar": outcome.request_id, "rule": outcome.rule_text,
            "by": caller.user_id,
        },
    )
    record_audit(
        db, action="folio_adjustment.raised", entity_type="folio",
        entity_id=str(folio_id), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.remarks,
        after={"adjustment_id": str(adj_id), "kind": body.kind,
               "amount": str(body.amount), "tax": str(tax_part),
               "reason": body.reason, "approval_required": outcome.required,
               "approval_request_id": str(outcome.request_id)},
    )
    return Decided(
        id=adj_id,
        status="pending_approval" if outcome.required else "approved",
        approval_required=outcome.required,
        policy_rule_text=outcome.rule_text,
        balance_after=_balance(db, folio_id),
        message=(
            _approval_sentence(outcome.approver_roles)
            + " Nothing has been taken off the folio yet."
            if outcome.required else
            "Cleared under policy and ready to post. The folio has not changed yet."
        ),
    )


# --------------------------------------------------------------------------
# Posting it — the only place the folio actually moves
# --------------------------------------------------------------------------
@adjustment_router.post("/folio-adjustments/{adjustment_id}/post", response_model=Decided)
def post_adjustment(
    adjustment_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Post the credit. Refused unless the approval actually came through."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    adj = db.execute(
        text("SELECT * FROM finance.folio_adjustments "
             "WHERE id = :id AND property_id = :p"),
        {"id": adjustment_id, "p": property_id},
    ).mappings().first()
    if adj is None:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    if adj["status"] == "posted":
        raise HTTPException(status_code=409, detail="That adjustment is already posted.")
    if adj["status"] in ("rejected", "reversed"):
        raise HTTPException(
            status_code=409, detail=f"That adjustment was {adj['status']}.")

    if adj["approval_required"]:
        state = approval_state(db, adj["approval_request_id"])
        if state is None or state["status"] != "approved":
            where = state["status"] if state else "missing"
            if where == "rejected":
                db.execute(
                    text("UPDATE finance.folio_adjustments SET status = 'rejected', "
                         "updated_at = now(), version = version + 1 WHERE id = :id"),
                    {"id": adjustment_id},
                )
                raise HTTPException(
                    status_code=409,
                    detail="That adjustment was rejected, so it cannot be posted.",
                )
            raise HTTPException(
                status_code=409,
                detail=f"This adjustment is still {where} approval. "
                       f"{adj['policy_rule_text']}",
            )

    business_date = db.execute(
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

    # The credit that undoes the charge. It points back at the original entry,
    # which is what makes the pair legible on the bill and in any later query.
    entry_id = uuid.uuid4()
    total = adj["amount"] + adj["tax_amount"]
    db.execute(
        text(
            """
            INSERT INTO finance.folio_entries
                (id, organization_id, property_id, folio_id, entry_type, amount,
                 currency, business_date, reversal_of_id, source_type, source_id,
                 source_line_key)
            VALUES (:id, :org, :prop, :f, 'credit', :amt, :cur, :bd, :rev,
                    'adjustment', :src, :slk)
            """
        ),
        {"id": entry_id, "org": adj["organization_id"], "prop": property_id,
         "f": adj["folio_id"], "amt": total, "cur": adj["currency"],
         "bd": business_date, "rev": adj["folio_entry_id"],
         "src": str(adjustment_id),
         "slk": f"adjustment:{adjustment_id}"},
    )
    db.execute(
        text("UPDATE finance.folio_adjustments SET status = 'posted', "
             "posted_entry_id = :e, posted_at = now(), posted_by = :u, "
             "updated_at = now(), version = version + 1 WHERE id = :id"),
        {"e": entry_id, "u": caller.user_id, "id": adjustment_id},
    )
    balance = _balance(db, adj["folio_id"])
    record_audit(
        db, action="folio_adjustment.posted", entity_type="folio",
        entity_id=str(adj["folio_id"]), organization_id=adj["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        before={"status": adj["status"]},
        after={"adjustment_id": str(adjustment_id), "entry_id": str(entry_id),
               "amount": str(total), "balance_after": str(balance)},
    )
    return Decided(
        id=adjustment_id, status="posted", approval_required=adj["approval_required"],
        policy_rule_text=adj["policy_rule_text"] or "",
        balance_after=balance,
        message=f"{KIND_LABELS[adj['kind']]} of {total:,.2f} posted. The folio "
                f"balance is now {balance:,.2f}.",
    )


@adjustment_router.post(
    "/folio-adjustments/{adjustment_id}/reverse", response_model=Decided
)
def reverse_adjustment(
    adjustment_id: uuid.UUID,
    property_id: uuid.UUID,
    remarks: str = Query(..., min_length=5, max_length=1000),
    caller: Caller = Depends(require_permission("payments", "cancel")),
    db: Session = Depends(get_session),
):
    """Undo a posted adjustment by posting a debit back.

    The original adjustment and its credit both stay. Deleting either would
    leave a folio whose history no longer explains its own balance.
    """
    assert_property_in_org(db, caller, property_id)
    adj = db.execute(
        text("SELECT * FROM finance.folio_adjustments "
             "WHERE id = :id AND property_id = :p"),
        {"id": adjustment_id, "p": property_id},
    ).mappings().first()
    if adj is None:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    if adj["status"] != "posted":
        raise HTTPException(
            status_code=409,
            detail="Only a posted adjustment can be reversed; this one is "
                   f"{adj['status'].replace('_', ' ')}.",
        )

    # The trading day, not the server's UTC date. This is a business date on
    # a folio entry, so it follows the same rule as every other one; see the
    # note on `_trading_day`.
    business_date = _trading_day(db, property_id)
    total = adj["amount"] + adj["tax_amount"]
    entry_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folio_entries
                (id, organization_id, property_id, folio_id, entry_type, amount,
                 currency, business_date, reversal_of_id, source_type, source_id,
                 source_line_key)
            VALUES (:id, :org, :prop, :f, 'debit', :amt, :cur, :bd, :rev,
                    'adjustment', :src, :slk)
            """
        ),
        {"id": entry_id, "org": adj["organization_id"], "prop": property_id,
         "f": adj["folio_id"], "amt": total, "cur": adj["currency"],
         "bd": business_date, "rev": adj["posted_entry_id"],
         "src": str(adjustment_id),
         "slk": f"adjustment-reversal:{adjustment_id}"},
    )
    db.execute(
        text("UPDATE finance.folio_adjustments SET status = 'reversed', "
             "remarks = remarks || ' | Reversed: ' || CAST(:r AS varchar), "
             "updated_at = now(), version = version + 1 WHERE id = :id"),
        {"r": remarks, "id": adjustment_id},
    )
    balance = _balance(db, adj["folio_id"])
    record_audit(
        db, action="folio_adjustment.reversed", entity_type="folio",
        entity_id=str(adj["folio_id"]), organization_id=adj["organization_id"],
        property_id=property_id, actor_subject=caller.subject, reason=remarks,
        after={"adjustment_id": str(adjustment_id), "entry_id": str(entry_id),
               "amount": str(total), "balance_after": str(balance)},
    )
    return Decided(
        id=adjustment_id, status="reversed",
        approval_required=adj["approval_required"],
        policy_rule_text=adj["policy_rule_text"] or "",
        balance_after=balance,
        message=f"Adjustment reversed; {total:,.2f} is back on the folio. "
                f"The balance is now {balance:,.2f}.",
    )


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------
@adjustment_router.post("/folio-adjustments/{adjustment_id}/evidence")
async def upload_evidence(
    adjustment_id: uuid.UUID,
    property_id: uuid.UUID,
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Attach the bill, guest request or void slip behind the adjustment."""
    assert_property_in_org(db, caller, property_id)
    adj = db.execute(
        text("SELECT id, organization_id, evidence_key, status "
             "FROM finance.folio_adjustments WHERE id = :id AND property_id = :p"),
        {"id": adjustment_id, "p": property_id},
    ).mappings().first()
    if adj is None:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    if adj["status"] in ("posted", "reversed"):
        raise HTTPException(
            status_code=409,
            detail="Evidence cannot be changed once the adjustment is posted.",
        )

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_DOC_TYPES:
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, WebP or PDF.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="That file is empty.")
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Keep the file under {MAX_DOC_BYTES // (1024 * 1024)}MB.",
        )

    key = build_key("folio-adjustments", str(adjustment_id), "evidence",
                    content_type=content_type)
    try:
        put_object(_STORE, key, data, content_type)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    old = adj["evidence_key"]
    db.execute(
        text("UPDATE finance.folio_adjustments SET evidence_key = :k, "
             "evidence_name = :n, updated_at = now() WHERE id = :id"),
        {"k": key, "n": file.filename, "id": adjustment_id},
    )
    if old:
        try:
            delete_object(_STORE, old)
        except Exception:  # noqa: BLE001 — the row already points at the new one
            pass
    record_audit(
        db, action="folio_adjustment.evidence_attached", entity_type="folio_adjustment",
        entity_id=str(adjustment_id), organization_id=adj["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"file": file.filename, "size": len(data)},
    )
    return {"name": file.filename, "url": _evidence_url(key)}
