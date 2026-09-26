"""Where the gateway tells us money actually moved.

This is the only place a guest-initiated payment is believed. Not the redirect
back to the site — a guest can visit the success URL by typing it, and a
booking engine that credits a folio on arrival at ``/thanks`` will be found out
within a week. The webhook is signed with a shared secret and comes from the
gateway, so it is evidence; the redirect is a hint that the guest is finished
looking at Razorpay's page.

Three things have to be true of anything handling one:

* **Verify the signature before reading the body.** An unsigned POST to this
  URL is an instruction from a stranger to credit somebody's account.
* **Assume duplicates.** Delivery is at least once. A retry after a timeout is
  indistinguishable from a new event except by its id, so the id is recorded
  before acting and checked before acting again.
* **Answer 200 even when ignoring it.** A gateway that gets an error retries,
  and retrying an event we have deliberately skipped forever is a queue that
  never drains. Refusal is for a bad signature, nothing else.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from decimal import Decimal

import httpx

from chirala_common.db import bind_tenant_context, system_context
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from chirala_common.property_time import local_today

from .credentials import by_webhook_ref
from .database import get_session
from .ledger import Allocation, post_payment
from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("webhook")

webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"],
                           route_class=TransactionalRoute)

#: Razorpay's payment methods, mapped onto the vocabulary the desk uses.
#:
#: The desk's list is short and concrete -- cash, card, UPI, bank transfer,
#: cheque, wallet -- because each one is a thing that happens at a counter. A
#: gateway reports its own taxonomy, and recording every online payment as
#: "online" collapsed all of it into one bucket: the cashiering screen showed
#: tens of thousands collected with nothing against Cards or UPI, which is
#: exactly the breakdown somebody opens that screen to read.
#:
#: EMI is a card underneath, and netbanking settles like a transfer. Anything
#: not listed stays ``online`` on purpose -- an honest "we know it was paid
#: online" beats filing a pay-later product under Wallet because it was the
#: nearest-looking box.
METHOD_MAP = {
    "card": "card",
    "emi": "card",
    "cardless_emi": "card",
    "upi": "upi",
    "netbanking": "bank_transfer",
    "bank_transfer": "bank_transfer",
    "wallet": "wallet",
}


def payment_method(entity: dict) -> str:
    """What the guest actually paid with, as the desk would name it."""
    return METHOD_MAP.get((entity.get("method") or "").lower().strip(),
                          "online")


#: Events worth acting on. Anything else is recorded and ignored — a gateway
#: sends a great deal that is none of the ledger's business, and silently
#: dropping it without a trace makes "did you get our webhook" unanswerable.
HANDLED = {"payment.captured", "refund.processed", "payment.failed"}


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Razorpay signs the raw body with HMAC-SHA256.

    The *raw* body: re-serialising the parsed JSON changes whitespace and key
    order, and the signature stops matching for reasons that look like an
    attack and are not.

    Compared with ``compare_digest`` rather than ``==``. A plain comparison
    returns as soon as two bytes differ, and the time it takes leaks how much
    of a guess was right — enough, over many attempts, to find the whole
    signature.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@webhook_router.post("/razorpay/{webhook_ref}",
                     status_code=status.HTTP_200_OK)
async def razorpay_tenant_webhook(
    webhook_ref: str,
    request: Request,
    db: Session = Depends(get_session),
):
    """Receive one Razorpay event for one tenant.

    The tenant is named in the path, and that is the only way this can work.
    Verifying a signature requires knowing which secret to verify against, and
    the only place that could otherwise come from is the body -- which is
    exactly the thing not yet trusted. So each tenant pastes its own URL into
    its own Razorpay dashboard, and the path settles whose secret applies before
    a byte of the body is read.

    An unknown or not-yet-enabled ref gets 404, the same as a wrong URL. It is
    not a secret, but it is also not a directory of which tenants exist.
    """
    # Whose callback this is cannot be known until its URL is looked up, so
    # this one lookup reads across tenants -- by design, and nothing else.
    system_context(db, reason="payment webhook: resolve tenant from callback URL")
    found = by_webhook_ref(db, webhook_ref)
    if found is None:
        log.warning("razorpay callback for unknown webhook ref")
        raise HTTPException(status_code=404, detail="Unknown callback.")
    organization_id, secret = found
    return await _receive(request, db, secret=secret,
                          expect_org=organization_id)


@webhook_router.post("/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_session),
):
    """Receive one Razorpay event on the deployment-wide callback.

    Kept for the single-hotel deployment that configured
    ``RAZORPAY_WEBHOOK_SECRET`` before tenants had their own gateways. It
    accepts an event for whichever tenant the order belongs to, which is only
    safe because one account means one tenant. A platform should give each
    tenant the per-tenant URL above instead; this one has no way to tell whose
    callback it is holding.
    """
    if not settings.razorpay_webhook_secret:
        # Refusing is the safe answer. Accepting unsigned events "until the
        # secret is configured" is how a test endpoint ends up in production
        # crediting folios for anyone who finds the URL.
        log.error("razorpay webhook received but no secret is configured")
        raise HTTPException(
            status_code=503,
            detail="Webhooks are not configured on this deployment.")
    return await _receive(request, db,
                          secret=settings.razorpay_webhook_secret,
                          expect_org=None)


async def _receive(
    request: Request,
    db: Session,
    *,
    secret: str,
    expect_org,
):
    """Verify, de-duplicate and act on one event.

    ``expect_org`` is the tenant the callback was addressed to, or ``None`` on
    the deployment-wide route. When set, an event is only allowed to settle that
    tenant's own intent -- see the check at the match below.
    """
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if not verify_signature(body, signature, secret):
        log.warning("razorpay webhook rejected: bad signature")
        raise HTTPException(status_code=400, detail="Invalid signature.")

    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed body.")

    # Razorpay puts the id in a header; older payloads carry it in the body.
    event_id = (request.headers.get("x-razorpay-event-id")
                or event.get("id") or "")
    event_type = event.get("event", "")
    if not event_id:
        # Without an id there is no way to tell a retry from a new event, and
        # acting on it risks doing the same thing twice.
        log.warning("razorpay webhook without an event id: %s", event_type)
        raise HTTPException(status_code=400, detail="Missing event id.")

    # The event log has no tenant, and the order it names has not been
    # matched to one yet: both are read in system context until the intent
    # is found, and not a moment after.
    system_context(db, reason="payment webhook: claim event and match its order")

    # Claim the event before doing anything. The insert is the lock: a
    # duplicate delivery loses the race on the primary key rather than racing
    # us to credit the same folio.
    claimed = db.execute(
        text(
            """
            INSERT INTO finance.provider_events
                (provider, event_id, event_type, payload, outcome)
            VALUES ('razorpay', :eid, :etype, CAST(:payload AS jsonb),
                    'processing')
            ON CONFLICT (provider, event_id) DO NOTHING
            RETURNING event_id
            """
        ),
        {"eid": event_id, "etype": event_type, "payload": json.dumps(event)},
    ).first()

    if claimed is None:
        # Seen before. 200, so the gateway stops retrying something already
        # dealt with.
        log.info("razorpay event %s already handled", event_id)
        return {"status": "duplicate", "event_id": event_id}

    if event_type not in HANDLED:
        db.execute(
            text("UPDATE finance.provider_events SET outcome = 'ignored' "
                 "WHERE provider = 'razorpay' AND event_id = :eid"),
            {"eid": event_id},
        )
        return {"status": "ignored", "event_id": event_id}

    if event_type != "payment.captured":
        # refund.processed and payment.failed are recorded so a retry is
        # recognised; acting on them is separate work with its own decisions
        # (a failed payment does not release a hold — the hold's own expiry
        # does that, and racing it would cancel a guest still typing).
        _finish(db, event_id, "recorded", detail=f"{event_type} recorded.")
        return {"status": "recorded", "event_id": event_id,
                "event": event_type}

    entity = (event.get("payload", {}).get("payment", {}).get("entity", {}))
    order_id = entity.get("order_id")
    if not order_id:
        # Malformed, not unmatched: with no order reference there is nothing
        # to reconcile against and no sum to place. Kept rather than dropped,
        # because a gateway sending these is itself worth knowing about.
        _finish(db, event_id, "malformed",
                detail="Capture carried no order id.")
        return {"status": "malformed", "event_id": event_id}

    intent = db.execute(
        text(
            """
            SELECT i.id, i.organization_id, i.property_id, i.reservation_id,
                   i.folio_id, i.expected_amount, i.currency, i.status
            FROM finance.payment_intents i
            WHERE i.provider_order_id = :o
            FOR UPDATE
            """
        ),
        {"o": order_id},
    ).mappings().first()
    if intent is None:
        # Money received against nothing we opened. This is the one outcome
        # on this route that means a person has to look: a real order
        # reference the gateway captured against, and no intent of ours to
        # match it to. Recorded loudly rather than dropped -- it is somebody's
        # payment, and the only way anyone finds it later is if we kept it.
        log.error("razorpay capture for unknown order %s", order_id)
        _finish(db, event_id, "unmatched",
                detail=f"No payment intent for order {order_id}.")
        return {"status": "unmatched", "event_id": event_id}

    if expect_org is not None and intent["organization_id"] != expect_org:
        # The callback came in on one tenant's URL, signed with one tenant's
        # secret, naming an order that belongs to another. Either two tenants
        # share a gateway account or somebody is replaying a neighbour's event;
        # either way this must not credit anything. Recorded, not acted on.
        log.error("razorpay callback on tenant %s carried order %s belonging "
                  "to tenant %s", expect_org, order_id,
                  intent["organization_id"])
        _finish(db, event_id, "wrong_tenant",
                detail=f"Order {order_id} belongs to another tenant.")
        return {"status": "unmatched", "event_id": event_id}

    if intent["status"] == "succeeded":
        _finish(db, event_id, "duplicate",
                detail="Intent already settled.", payment_id=None)
        return {"status": "duplicate", "event_id": event_id}

    # From here on this transaction touches money, and it is that tenant's
    # money only: row security now holds the posting to the intent's tenant.
    bind_tenant_context(db, organization_id=intent["organization_id"],
                        property_id=intent["property_id"], is_service=True)

    # Trust the gateway's amount, not our own expectation. A guest who paid a
    # different figure has paid what they paid; crediting the folio with what
    # we hoped for would invent money.
    paid = Decimal(str(entity.get("amount", 0))) / 100
    if paid <= 0:
        _finish(db, event_id, "malformed", detail="Capture had no amount.")
        return {"status": "malformed", "event_id": event_id}

    # The oldest day not yet closed, else the property's own date -- not
    # CURRENT_DATE, which is UTC and a day behind India until 05:30.
    business_date = db.execute(
        text("SELECT min(business_date) "
             "FROM finance.business_days WHERE property_id = :p "
             "AND status <> 'closed'"),
        {"p": intent["property_id"]},
    ).scalar() or local_today(db, intent["property_id"])

    result = post_payment(
        db,
        organization_id=intent["organization_id"],
        property_id=intent["property_id"],
        # The instrument the guest actually used, not just "online" -- the
        # cashiering screen breaks the day's takings down by method, and one
        # opaque bucket makes that breakdown useless.
        method=payment_method(entity),
        business_date=business_date,
        allocations=[Allocation(folio_id=intent["folio_id"], amount=paid)],
        currency=intent["currency"],
        intent_id=intent["id"],
        # The gateway's own payment id, and the money it refers to is already
        # taken. Passing it stops the ledger trying to capture a second time
        # and, just as importantly, records the id the gateway knows this
        # payment by -- without it the row carried a synthetic one and no
        # statement could ever be reconciled against ours.
        settled_transaction_id=entity.get("id") or None,
        # A guest paying on the booking engine at two in the morning is not
        # the front desk taking money, and the cashiering screen totals by
        # source -- left as the default, these would turn up in a shift's
        # drawer count that never handled them.
        source="booking_engine",
    )
    db.execute(
        text("UPDATE finance.payment_intents SET status = 'succeeded', "
             "updated_at = now() WHERE id = :id"),
        {"id": intent["id"]},
    )

    # The booking becomes real here and nowhere else.
    confirmed, why, final = await confirm_booking(intent["reservation_id"],
                                                  intent["organization_id"])
    if confirmed:
        outcome, detail = "settled", f"Credited {paid}."
    elif final:
        # booking-core has *decided*: the hold expired and its rooms were sold
        # before the guest's money arrived, or the booking was cancelled.
        # Retrying will never confirm it. The guest has paid for a room they
        # will not get, so this is recorded as a refund owed rather than
        # folded in with a network blip -- the two need different people
        # doing different things, and "paid_unconfirmed" said neither.
        outcome = "paid_needs_refund"
        detail = (f"Credited {paid}, but the booking cannot be confirmed: "
                  f"{why} Refund the guest from the folio (the payment is on "
                  f"it), or move the money to a new booking.")
    else:
        outcome = "paid_unconfirmed"
        detail = (f"Credited {paid}, but confirming failed: {why}. The money "
                  f"is on the folio; the booking is still held. Confirm it "
                  f"from the reservation once booking-core is reachable.")
    _finish(db, event_id, outcome, payment_id=result.payment_id,
            detail=detail)
    if not confirmed:
        # Paid but unconfirmed is the one state that needs a person. Loud, and
        # left in the record for them to find.
        log.error("order %s paid but reservation %s not confirmed (%s): %s",
                  order_id, intent["reservation_id"], outcome, why)
    log.info("razorpay event %s settled order %s (%s)", event_id, order_id, paid)
    return {"status": outcome, "event_id": event_id}


def _finish(db: Session, event_id: str, outcome: str, *,
            detail: str | None = None, payment_id=None) -> None:
    system_context(db, reason="payment webhook: record event outcome")
    db.execute(
        text("UPDATE finance.provider_events SET outcome = :o, detail = :d, "
             "payment_id = :pid "
             "WHERE provider = 'razorpay' AND event_id = :eid"),
        {"o": outcome, "d": detail, "pid": payment_id, "eid": event_id},
    )


async def confirm_booking(reservation_id, organization_id
                          ) -> tuple[bool, str, bool]:
    """Ask booking-core to turn the hold into a booking.

    Returns ``(confirmed, why, final)``. ``final`` says booking-core answered
    and refused -- the booking cannot be confirmed, now or on a retry -- as
    against a failure to ask at all.

    Asynchronous, because the webhook handler is: a blocking ``httpx.post``
    inside it stalled the whole event loop for up to fifteen seconds, and with
    it every other request this worker was serving.

    Over HTTP with the platform's own credential rather than reimplementing
    it here. Confirming moves held nights to reserved under row locks and
    converts the hold; this service already carries three copies of inventory
    logic it does not own, and a fourth in the path that takes money is the
    one place that mistake would be most expensive.

    A failure is reported, never raised. The money is already posted and the
    event already recorded — turning this into a 500 would make the gateway
    retry a payment that has been credited.
    """
    from .settings import settings

    if not settings.service_token:
        return False, "no service credential configured", False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.booking_url}/reservations/{reservation_id}/confirm",
                headers={"X-Service-Token": settings.service_token,
                         "X-Service-Org": str(organization_id)},
            )
    except httpx.HTTPError as exc:
        return False, str(exc), False
    if resp.status_code in (404, 409, 422):
        try:
            why = str(resp.json().get("detail") or resp.text[:300])
        except ValueError:
            why = resp.text[:300]
        return False, why, True
    if resp.status_code >= 400:
        return False, f"{resp.status_code} {resp.text[:160]}", False
    return True, "", False
