"""Card holds: authorise an amount on a guest's card, capture it later.

A deposit against the minibar and damage, or a guarantee on a booking. The
guest's card is authorised for an amount through Razorpay Checkout and
nothing is taken. At check-out the hotel captures what is owed, all of the
hold or part of it, and that capture posts to the folio. Or the hotel
releases the hold and takes nothing.

The card never touches this system. The guest types it into Razorpay's own
checkout, and what comes back is a payment id. That is why there is no
card number, expiry or CVV anywhere in the database, and no PCI scope
beyond what Razorpay carries.

The browser is believed for nothing:

* **Authorised** is set only after the checkout's signature verifies against
  the key secret, and Razorpay's own record of the payment says
  ``authorized``, for this order, for the amount asked.
* **Captured** is posted to the folio only after Razorpay confirms the
  capture, and with Razorpay's payment id, so the ledger can be reconciled
  against the gateway's statement.

The intent row is locked for every change, so two clicks on Capture cannot
capture twice.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .credentials import for_organization
from .database import get_session
from .ledger import Allocation, LedgerError, post_payment
from .razorpay_provider import PaymentLinkError, RazorpayNotConfigured, RazorpayProvider
from .routes import _trading_day
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

hold_router = APIRouter(tags=["card-holds"], route_class=TransactionalRoute)

#: How long a hold lasts before Razorpay returns the money on its own, in
#: minutes. Five days covers a normal stay.
HOLD_EXPIRY_MINUTES = 7200


def _provider(db: Session, organization_id) -> RazorpayProvider:
    cfg = for_organization(db, organization_id)
    if not cfg.live:
        raise HTTPException(409, "Card holds need a connected Razorpay account. "
                                 "Add it under Payment Gateway.")
    return RazorpayProvider(cfg.key_id, cfg.key_secret)


def _paise(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1")))


_SELECT = """
    SELECT i.id, i.reservation_id, i.expected_amount AS amount,
           i.authorized_amount, i.captured_amount, i.currency, i.status,
           i.purpose, i.created_at, i.updated_at, i.created_by,
           i.provider_order_id, i.authorized_payment_id
    FROM finance.payment_intents i
"""


def _hold(db: Session, hold_id: uuid.UUID, property_id: uuid.UUID, *, lock: bool = False):
    row = db.execute(
        text("SELECT i.*, r.number FROM finance.payment_intents i "
             "JOIN booking.reservations r ON r.id = i.reservation_id "
             "WHERE i.id = :i AND i.property_id = :p AND i.kind = 'hold'"
             + (" FOR UPDATE OF i" if lock else "")),
        {"i": hold_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "Card hold not found")
    return row


def _view(db: Session, hold_id: uuid.UUID) -> dict:
    return dict(db.execute(text(_SELECT + " WHERE i.id = :i"), {"i": hold_id}).mappings().one())


class HoldIn(BaseModel):
    reservation_id: uuid.UUID
    amount: Decimal = Field(gt=0, le=Decimal("1000000"))
    purpose: str | None = Field(default=None, max_length=200)


@hold_router.post("/card-holds", status_code=201)
def create_hold(property_id: uuid.UUID, body: HoldIn,
                caller: Caller = Depends(require_permission("payments", "edit")),
                db: Session = Depends(get_session)):
    """Open a hold. Returns what Razorpay Checkout needs to take the card."""
    assert_property_in_org(db, caller, property_id)
    res = db.execute(text("""
        SELECT r.id, r.number, r.status, r.organization_id, r.currency,
               g.full_name, g.email, g.phone, p.name AS property_name
        FROM booking.reservations r
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        JOIN iam.properties p ON p.id = r.property_id
        WHERE r.id = :r AND r.property_id = :p
    """), {"r": body.reservation_id, "p": property_id}).mappings().first()
    if res is None:
        raise HTTPException(404, "Reservation not found")
    if res["status"] in ("cancelled", "no_show"):
        raise HTTPException(409, f"{res['number']} is {res['status']}.")
    provider = _provider(db, res["organization_id"])
    intent_id = uuid.uuid4()
    currency = res["currency"] or "INR"
    try:
        order = provider.create_hold_order(
            amount=body.amount, currency=currency, receipt=f"hold-{intent_id.hex[:20]}",
            notes={"myguest_hold": str(intent_id), "reservation": res["number"]},
            expiry_minutes=HOLD_EXPIRY_MINUTES)
    except (PaymentLinkError, RazorpayNotConfigured) as exc:
        raise HTTPException(502, f"Razorpay refused the hold: {exc}") from None
    folio_id = db.execute(text("SELECT id FROM finance.folios WHERE reservation_id = :r "
                               "AND status = 'open' ORDER BY created_at LIMIT 1"),
                          {"r": res["id"]}).scalar()
    purpose = (body.purpose or "").strip() or "Security deposit"
    db.execute(text("""
        INSERT INTO finance.payment_intents
            (id, organization_id, property_id, reservation_id, folio_id,
             expected_amount, currency, status, idempotency_key, kind,
             provider_order_id, purpose, created_by)
        VALUES (:id, :org, :p, :r, :f, :amt, :cur, 'created', :key, 'hold',
                :order, :purpose, :by)
    """), {"id": intent_id, "org": res["organization_id"], "p": property_id,
           "r": res["id"], "f": folio_id, "amt": body.amount, "cur": currency,
           "key": f"hold:{intent_id}", "order": order.order_id, "purpose": purpose,
           "by": caller.subject})
    return {"id": intent_id, "order_id": order.order_id, "key_id": order.key_id,
            "amount_paise": order.amount_paise, "currency": currency,
            "description": f"{res['property_name']}: {purpose} ({res['number']})",
            "prefill": {"name": res["full_name"] or "", "email": res["email"] or "",
                        "contact": res["phone"] or ""}}


class ConfirmIn(BaseModel):
    payment_id: str = Field(min_length=5, max_length=120)
    signature: str = Field(min_length=10, max_length=200)


@hold_router.post("/card-holds/{hold_id}/confirm")
def confirm_hold(hold_id: uuid.UUID, property_id: uuid.UUID, body: ConfirmIn,
                 caller: Caller = Depends(require_permission("payments", "edit")),
                 db: Session = Depends(get_session)):
    """Checkout says the card was authorised. Check that with Razorpay."""
    assert_property_in_org(db, caller, property_id)
    h = _hold(db, hold_id, property_id, lock=True)
    if h["status"] == "authorized" and h["authorized_payment_id"] == body.payment_id:
        return _view(db, hold_id)
    if h["status"] != "created":
        raise HTTPException(409, f"This hold is {h['status']}.")
    provider = _provider(db, h["organization_id"])
    if not provider.checkout_signature_ok(h["provider_order_id"], body.payment_id,
                                          body.signature):
        raise HTTPException(400, "The payment signature did not verify.")
    try:
        pay = provider.fetch_payment(body.payment_id)
    except (PaymentLinkError, RazorpayNotConfigured) as exc:
        raise HTTPException(502, f"Razorpay could not confirm the payment: {exc}") from None
    if (pay.get("order_id") != h["provider_order_id"]
            or pay.get("status") != "authorized"
            or int(pay.get("amount") or 0) != _paise(Decimal(h["expected_amount"]))):
        raise HTTPException(409, "Razorpay does not show this card as authorised for "
                                 f"this hold (status {pay.get('status')}).")
    db.execute(text("""
        UPDATE finance.payment_intents SET status = 'authorized',
               authorized_payment_id = :pay, authorized_amount = expected_amount,
               updated_at = now()
        WHERE id = :i
    """), {"pay": body.payment_id, "i": hold_id})
    record_audit(db, action="card_hold.authorized", entity_type="payment_intent",
                 entity_id=str(hold_id), organization_id=h["organization_id"],
                 property_id=property_id, actor_subject=caller.subject,
                 after={"reservation": h["number"], "amount": str(h["expected_amount"]),
                        "method": pay.get("method")})
    return _view(db, hold_id)


@hold_router.get("/reservations/{reservation_id}/card-holds")
def list_holds(reservation_id: uuid.UUID, property_id: uuid.UUID,
               caller: Caller = Depends(require_permission("payments", "view")),
               db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    return [dict(r) for r in db.execute(
        text(_SELECT + " WHERE i.reservation_id = :r AND i.property_id = :p "
                       "AND i.kind = 'hold' ORDER BY i.created_at DESC"),
        {"r": reservation_id, "p": property_id}).mappings()]


class CaptureIn(BaseModel):
    amount: Decimal = Field(gt=0)


@hold_router.post("/card-holds/{hold_id}/capture")
def capture_hold(hold_id: uuid.UUID, property_id: uuid.UUID, body: CaptureIn,
                 caller: Caller = Depends(require_permission("payments", "create")),
                 db: Session = Depends(get_session)):
    """Take some or all of the held amount, and post it to the guest's folio."""
    assert_property_in_org(db, caller, property_id)
    h = _hold(db, hold_id, property_id, lock=True)
    if h["status"] != "authorized":
        raise HTTPException(409, f"Only an authorised hold can be captured; this one is {h['status']}.")
    if body.amount > Decimal(h["authorized_amount"]):
        raise HTTPException(422, f"You can capture at most {h['authorized_amount']}.")
    folio_id = h["folio_id"] or db.execute(
        text("SELECT id FROM finance.folios WHERE reservation_id = :r AND status = 'open' "
             "ORDER BY created_at LIMIT 1"), {"r": h["reservation_id"]}).scalar()
    if folio_id is None:
        raise HTTPException(409, "This booking has no open bill to post the capture to.")
    provider = _provider(db, h["organization_id"])
    result = provider.capture(amount=body.amount, currency=h["currency"], method="card",
                              reference=h["authorized_payment_id"])
    if not result.success:
        raise HTTPException(502, f"Razorpay did not capture: {result.detail}")
    try:
        paid = post_payment(
            db, organization_id=h["organization_id"], property_id=property_id,
            method="card", business_date=_trading_day(db, property_id),
            allocations=[Allocation(folio_id=folio_id, amount=body.amount)],
            currency=h["currency"], intent_id=hold_id,
            settled_transaction_id=h["authorized_payment_id"],
            reference=h["authorized_payment_id"],
            note=f"Captured from card hold ({h['purpose'] or 'deposit'})",
            posted_by=caller.subject)
    except LedgerError as exc:
        # Razorpay has taken the money and the ledger refused it. Loud: this
        # needs a person, and the gateway's payment id is in the error.
        raise HTTPException(409, f"Captured at Razorpay ({h['authorized_payment_id']}) "
                                 f"but the folio refused it: {exc}") from exc
    db.execute(text("""
        UPDATE finance.payment_intents SET status = 'succeeded',
               captured_amount = :amt, folio_id = :f, updated_at = now()
        WHERE id = :i
    """), {"amt": body.amount, "f": folio_id, "i": hold_id})
    record_audit(db, action="card_hold.captured", entity_type="payment_intent",
                 entity_id=str(hold_id), organization_id=h["organization_id"],
                 property_id=property_id, actor_subject=caller.subject,
                 after={"reservation": h["number"], "captured": str(body.amount),
                        "of": str(h["authorized_amount"]), "payment": str(paid.payment_id)})
    return _view(db, hold_id)


@hold_router.post("/card-holds/{hold_id}/release")
def release_hold(hold_id: uuid.UUID, property_id: uuid.UUID,
                 caller: Caller = Depends(require_permission("payments", "edit")),
                 db: Session = Depends(get_session)):
    """Take nothing. The authorisation lapses and the bank returns the money.

    Razorpay refunds an uncaptured authorisation once the order's manual
    expiry passes, so releasing is a record that the hotel will not capture,
    not a call to the gateway.
    """
    assert_property_in_org(db, caller, property_id)
    h = _hold(db, hold_id, property_id, lock=True)
    if h["status"] not in ("created", "authorized"):
        raise HTTPException(409, f"This hold is already {h['status']}.")
    db.execute(text("UPDATE finance.payment_intents SET status = 'released', "
                    "updated_at = now() WHERE id = :i"), {"i": hold_id})
    record_audit(db, action="card_hold.released", entity_type="payment_intent",
                 entity_id=str(hold_id), organization_id=h["organization_id"],
                 property_id=property_id, actor_subject=caller.subject,
                 before={"status": h["status"]}, after={"status": "released"})
    return _view(db, hold_id)
