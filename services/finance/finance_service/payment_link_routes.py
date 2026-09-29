"""Payment links: ask a guest who is not at the desk to pay.

A pay-at-hotel booking that should be secured before arrival, a deposit, a
balance left after an early departure. The front desk creates a link for an
amount against a reservation, and it goes to the guest's phone by SMS or
WhatsApp (see chirala_common.guest_messages). The guest pays on Razorpay's
page, and ``webhook_routes`` credits the folio when Razorpay says the money
moved. Nothing here marks anything paid.

A link is a payment intent of kind ``link``. The migration explains why that
is the whole data model: the webhook's one-time settlement, the tenant check
and row security all apply unchanged.

Two rules the gateway does not enforce for us:

* **One live link per reservation.** A second link while one is still open
  lets a guest pay twice, so a new one cancels the old one first, at Razorpay
  as well as here.
* **A mock gateway never texts a guest a link.** With no Razorpay account the
  link has no URL, and a message telling a guest to pay something that cannot
  take money is worse than no message. In messaging test mode nothing leaves
  the building anyway, so the flow can still be walked end to end.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.guest_messages import notify_guest, summary
from chirala_common.messenger import normalise_mobile
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .credentials import for_organization
from .database import SessionFactory, get_session
from .provider import provider_for
from .razorpay_provider import PaymentLinkError, RazorpayNotConfigured
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

payment_link_router = APIRouter(tags=["payment-links"],
                                route_class=TransactionalRoute)

LIVE = ("created", "processing")


class LinkIn(BaseModel):
    reservation_id: uuid.UUID
    amount: Decimal = Field(gt=0, le=Decimal("10000000"))
    purpose: str | None = Field(default=None, max_length=200)
    #: How long the guest has to pay. Razorpay needs at least 15 minutes.
    expires_in_hours: int = Field(default=72, ge=1, le=24 * 30)
    #: Text the link to the guest now, on the property's enabled channels.
    send: bool = True


class LinkOut(BaseModel):
    id: uuid.UUID
    reservation_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    purpose: str | None
    url: str | None
    provider_link_id: str | None
    expires_at: datetime | None
    created_at: datetime
    created_by: str | None
    #: True when no real gateway is configured: the link cannot take money.
    mock: bool
    #: What happened to the message, when one was attempted.
    message: str | None = None


_SELECT = """
    SELECT i.id, i.reservation_id, i.expected_amount AS amount, i.currency,
           i.status, i.purpose, i.link_url AS url, i.provider_link_id,
           i.expires_at, i.created_at, i.created_by,
           (i.provider_link_id LIKE 'mock_%') AS mock
    FROM finance.payment_intents i
"""


def _out(row, message: str | None = None) -> LinkOut:
    d = dict(row)
    # A live link past its expiry is expired, whether or not Razorpay's
    # webhook has said so yet. The screen should not offer to resend it.
    if (d["status"] in LIVE and d["expires_at"] is not None
            and d["expires_at"] < datetime.now(timezone.utc)):
        d["status"] = "expired"
    return LinkOut(**d, message=message)


def _contact(phone: str | None) -> str | None:
    digits = normalise_mobile(phone)
    return f"+{digits}" if digits else None


def _reservation(db: Session, reservation_id: uuid.UUID,
                 property_id: uuid.UUID):
    row = db.execute(
        text("""
            SELECT r.id, r.number, r.status, r.organization_id, r.currency,
                   g.full_name AS guest_name, g.phone, g.email,
                   p.name AS property_name
            FROM booking.reservations r
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            JOIN iam.properties p ON p.id = r.property_id
            WHERE r.id = :r AND r.property_id = :p
        """),
        {"r": reservation_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


def _folio(db: Session, res, property_id: uuid.UUID) -> uuid.UUID:
    found = db.execute(
        text("SELECT id FROM finance.folios WHERE reservation_id = :r "
             "AND status = 'open' ORDER BY created_at LIMIT 1"),
        {"r": res["id"]},
    ).scalar()
    if found:
        return found
    fid = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO finance.folios
                (id, organization_id, property_id, reservation_id, type,
                 currency, status)
            VALUES (:id, :org, :prop, :res, 'guest', :cur, 'open')
        """),
        {"id": fid, "org": res["organization_id"], "prop": property_id,
         "res": res["id"], "cur": res["currency"] or "INR"},
    )
    return fid


def _text_link(db: Session, res, property_id: uuid.UUID, *, amount: Decimal,
               url: str, mock: bool) -> str:
    if mock and not settings.messaging_config.test_mode:
        return ("Not sent: no payment gateway is connected, so this link "
                "cannot take money.")
    outcomes = notify_guest(
        db, SessionFactory, settings.messaging_config,
        code="guest_payment_link",
        organization_id=res["organization_id"], property_id=property_id,
        phone=res["phone"],
        values={
            "guest_name": res["guest_name"] or "Guest",
            "amount": f"Rs {amount:,.2f}",
            "reservation_number": res["number"],
            "property_name": res["property_name"],
            "link": url or "(mock link)",
        },
    )
    return summary(outcomes)


def _cancel_at_gateway(db: Session, organization_id, link_id: str | None) -> None:
    if not link_id or link_id.startswith("mock_"):
        return
    provider = provider_for(for_organization(db, organization_id))
    try:
        provider.cancel_payment_link(link_id)
    except (PaymentLinkError, RazorpayNotConfigured) as exc:
        # Already paid or expired at Razorpay is the usual reason, and either
        # way the link is no longer payable, which is what cancelling is for.
        raise HTTPException(
            status_code=409,
            detail=f"Razorpay would not cancel the link: {exc}") from None


@payment_link_router.post("/payment-links", response_model=LinkOut,
                          status_code=201)
def create_link(
    property_id: uuid.UUID,
    body: LinkIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    res = _reservation(db, body.reservation_id, property_id)
    if res["status"] in ("cancelled", "no_show"):
        raise HTTPException(
            status_code=409,
            detail=f"{res['number']} is {res['status']}; there is nothing to "
                   f"collect for.")

    # One live link per reservation. See the module docstring.
    for old in db.execute(
        text("SELECT id, provider_link_id FROM finance.payment_intents "
             "WHERE reservation_id = :r AND property_id = :p "
             "AND kind = 'link' AND status IN ('created', 'processing') "
             "FOR UPDATE"),
        {"r": res["id"], "p": property_id},
    ).mappings().all():
        _cancel_at_gateway(db, res["organization_id"], old["provider_link_id"])
        db.execute(
            text("UPDATE finance.payment_intents SET status = 'cancelled', "
                 "updated_at = now() WHERE id = :i"),
            {"i": old["id"]},
        )

    gateway = for_organization(db, res["organization_id"])
    provider = provider_for(gateway)
    intent_id = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    currency = res["currency"] or "INR"
    purpose = (body.purpose or "").strip() or f"Payment for booking {res['number']}"
    try:
        link = provider.create_payment_link(
            amount=body.amount, currency=currency,
            description=f"{res['property_name']}: {purpose}",
            reference_id=intent_id.hex[:32], expire_by=int(expires.timestamp()),
            # Razorpay wants 8-14 characters, so "+91 90000 00000" as typed at
            # the desk is refused. The same normaliser the messages use.
            customer={"name": res["guest_name"],
                      "contact": _contact(res["phone"]),
                      "email": res["email"]},
            notes={"myguest_intent": str(intent_id),
                   "reservation": res["number"]},
        )
    except (PaymentLinkError, RazorpayNotConfigured) as exc:
        raise HTTPException(status_code=502,
                            detail=f"Razorpay refused the link: {exc}") from None

    folio_id = _folio(db, res, property_id)
    db.execute(
        text("""
            INSERT INTO finance.payment_intents
                (id, organization_id, property_id, reservation_id, folio_id,
                 expected_amount, currency, status, idempotency_key, kind,
                 provider_link_id, link_url, expires_at, purpose, created_by)
            VALUES (:id, :org, :prop, :res, :folio, :amt, :cur, 'created',
                    :key, 'link', :lid, :url, :exp, :purpose, :by)
        """),
        {"id": intent_id, "org": res["organization_id"], "prop": property_id,
         "res": res["id"], "folio": folio_id, "amt": body.amount,
         "cur": currency, "key": f"link:{intent_id}", "lid": link.link_id,
         "url": link.url or None, "exp": expires, "purpose": purpose,
         "by": caller.subject},
    )

    message = None
    if body.send:
        message = _text_link(db, res, property_id, amount=body.amount,
                             url=link.url, mock=not gateway.live)
    record_audit(
        db, action="payment_link.created", entity_type="payment_intent",
        entity_id=str(intent_id), organization_id=res["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"reservation": res["number"], "amount": str(body.amount),
               "expires_at": expires.isoformat(), "mock": not gateway.live,
               "message": message},
    )
    row = db.execute(text(_SELECT + " WHERE i.id = :i"), {"i": intent_id}
                     ).mappings().first()
    return _out(row, message)


@payment_link_router.get("/reservations/{reservation_id}/payment-links",
                         response_model=list[LinkOut])
def list_links(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(_SELECT + " WHERE i.reservation_id = :r AND i.property_id = :p "
                       "AND i.kind = 'link' ORDER BY i.created_at DESC"),
        {"r": reservation_id, "p": property_id},
    ).mappings().all()
    return [_out(r) for r in rows]


def _link(db: Session, intent_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text("SELECT i.*, r.number FROM finance.payment_intents i "
             "JOIN booking.reservations r ON r.id = i.reservation_id "
             "WHERE i.id = :i AND i.property_id = :p AND i.kind = 'link' "
             "FOR UPDATE OF i"),
        {"i": intent_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment link not found")
    return row


@payment_link_router.post("/payment-links/{intent_id}/resend",
                          response_model=LinkOut)
def resend_link(
    intent_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    link = _link(db, intent_id, property_id)
    current = _out(db.execute(text(_SELECT + " WHERE i.id = :i"),
                              {"i": intent_id}).mappings().first())
    if current.status not in LIVE:
        raise HTTPException(status_code=409,
                            detail=f"This link is {current.status}; create a "
                                   f"new one instead.")
    res = _reservation(db, link["reservation_id"], property_id)
    message = _text_link(db, res, property_id, amount=link["expected_amount"],
                         url=link["link_url"] or "", mock=current.mock)
    record_audit(
        db, action="payment_link.resent", entity_type="payment_intent",
        entity_id=str(intent_id), organization_id=link["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"message": message},
    )
    return current.model_copy(update={"message": message})


@payment_link_router.post("/payment-links/{intent_id}/cancel",
                          response_model=LinkOut)
def cancel_link(
    intent_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    link = _link(db, intent_id, property_id)
    if link["status"] not in LIVE:
        raise HTTPException(status_code=409,
                            detail=f"This link is already {link['status']}.")
    _cancel_at_gateway(db, link["organization_id"], link["provider_link_id"])
    db.execute(
        text("UPDATE finance.payment_intents SET status = 'cancelled', "
             "updated_at = now() WHERE id = :i"),
        {"i": intent_id},
    )
    record_audit(
        db, action="payment_link.cancelled", entity_type="payment_intent",
        entity_id=str(intent_id), organization_id=link["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        before={"status": link["status"]}, after={"status": "cancelled"},
    )
    return _out(db.execute(text(_SELECT + " WHERE i.id = :i"),
                           {"i": intent_id}).mappings().first())
