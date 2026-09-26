"""Taking a guest's money for a booking they are holding.

Two halves of one conversation, minutes apart:

* **Before** — a payment intent is opened against the reservation and an order
  is created with the gateway. Nothing has been paid; this is the thing the
  guest's checkout will point at.
* **After** — the gateway says the money moved, and the webhook credits the
  folio and confirms the booking.

Nothing in between is trusted. The guest returning to a success page proves
they closed a browser tab, and a booking engine that confirms on that will be
found out the first time somebody types the URL.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from chirala_common.authz import Caller, require_property_permission
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .credentials import GatewayConfig, for_organization
from .provider import provider_for
from .routes import require_org_permission

checkout_router = APIRouter(prefix="/checkout", tags=["checkout"],
                            route_class=TransactionalRoute)


class IntentIn(BaseModel):
    #: No ``organization_id``. It decides whose gateway keys are used and
    #: therefore whose bank account the guest pays into, which makes it the one
    #: field a caller must not be able to choose. It is read from the
    #: reservation instead.
    property_id: uuid.UUID
    reservation_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    currency: str = "INR"


class IntentOut(BaseModel):
    """Everything the guest's browser needs to open a checkout."""

    intent_id: uuid.UUID
    order_id: str
    amount: Decimal
    currency: str
    #: The gateway's publishable key. Empty on the mock provider, which is how
    #: a caller can tell no real money will move.
    key_id: str = ""


@checkout_router.post("/intents", response_model=IntentOut)
def create_intent(
    body: IntentIn,
    caller: Caller = Depends(require_org_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Open an intent for a held booking, and an order to pay it against."""
    require_property_permission(db, caller, body.property_id,
                                "payments", "edit")

    res = db.execute(
        text("SELECT number, status, organization_id FROM booking.reservations "
             "WHERE id = :r AND property_id = :p"),
        {"r": body.reservation_id, "p": body.property_id},
    ).mappings().first()
    if res is None:
        raise HTTPException(status_code=404, detail="No such reservation.")

    if res["status"] not in ("held", "draft"):
        # Confirmed already, or cancelled. Either way, taking money for it
        # now would be taking money for something that is not on offer.
        raise HTTPException(
            status_code=409,
            detail=f"{res['number']} is {res['status']}; there is nothing to "
                   f"pay for.")

    # Whose gateway, and therefore whose bank account. Taken from the
    # reservation being paid for, not from the request: the property has
    # already been proved to belong to the caller's tenant, and the
    # reservation to that property, so this org is reachable only by a caller
    # entitled to it. A value carried in the body would be an assertion by the
    # caller that nothing downstream re-checks -- and the thing it would decide
    # is which merchant account the guest's money lands in.
    organization_id = res["organization_id"]

    # One intent per reservation. A guest who reloads the checkout should be
    # sent back to the same order, not given a second one to pay twice.
    existing = db.execute(
        text("SELECT id, provider_order_id, expected_amount, currency "
             "FROM finance.payment_intents "
             "WHERE reservation_id = :r AND status IN ('created', 'processing')"
             " ORDER BY created_at DESC LIMIT 1"),
        {"r": body.reservation_id},
    ).mappings().first()
    if existing and existing["provider_order_id"]:
        return IntentOut(
            intent_id=existing["id"], order_id=existing["provider_order_id"],
            amount=existing["expected_amount"], currency=existing["currency"],
            key_id=for_organization(db, organization_id).key_id,
        )

    gateway = for_organization(db, organization_id)

    folio_id = _folio_for(db, body, organization_id)
    intent_id = uuid.uuid4()
    order_id = _create_order(
        gateway, amount=body.amount, currency=body.currency,
        receipt=res["number"])

    db.execute(
        text(
            """
            INSERT INTO finance.payment_intents
                (id, organization_id, property_id, reservation_id, folio_id,
                 expected_amount, currency, status, idempotency_key,
                 provider_order_id)
            VALUES (:id, :org, :prop, :res, :folio, :amt, :cur, 'created',
                    :key, :order)
            """
        ),
        {"id": intent_id, "org": organization_id,
         "prop": body.property_id, "res": body.reservation_id,
         "folio": folio_id, "amt": body.amount, "cur": body.currency,
         "key": f"intent:{intent_id}", "order": order_id},
    )
    return IntentOut(intent_id=intent_id, order_id=order_id,
                     amount=body.amount, currency=body.currency,
                     key_id=gateway.key_id)


def _create_order(config: GatewayConfig, *, amount: Decimal, currency: str,
                  receipt: str) -> str:
    """An order at *this tenant's* gateway, or a stand-in when they have none.

    The mock's order id is deliberately obvious. A synthetic id that looked
    real would let a deployment run for weeks believing it was taking money.
    """
    provider = provider_for(config)
    order = getattr(provider, "create_order", None)
    if order is None:
        return f"mock_order_{uuid.uuid4().hex[:16]}"
    return order(amount=amount, currency=currency, receipt=receipt).order_id


def _folio_for(db: Session, body: IntentIn,
               organization_id: uuid.UUID) -> uuid.UUID:
    found = db.execute(
        text("SELECT id FROM finance.folios WHERE reservation_id = :r "
             "AND status = 'open' ORDER BY created_at LIMIT 1"),
        {"r": body.reservation_id},
    ).scalar()
    if found:
        return found
    fid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folios
                (id, organization_id, property_id, reservation_id, type,
                 currency, status)
            VALUES (:id, :org, :prop, :res, 'guest', :cur, 'open')
            """
        ),
        {"id": fid, "org": organization_id, "prop": body.property_id,
         "res": body.reservation_id, "cur": body.currency},
    )
    return fid
