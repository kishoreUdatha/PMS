"""Bookings arriving from a channel manager.

Channex fronts Booking.com, Agoda, Expedia and the rest, so the PMS integrates
once rather than once per OTA. A guest books on the OTA, the OTA tells Channex,
Channex tells us — and this is where it lands.

Four things have to be true of anything handling that:

* **Check the secret before reading the body.** Channex does not sign its
  payloads; it sends back a header you gave it. So that header is the whole of
  the check, and an unsigned POST to this URL is an instruction from a stranger
  to create a booking.
* **Claim the revision id before acting.** Delivery is at least once and
  Channex retries eleven times over roughly a day. A retry is indistinguishable
  from a new event except by its id, so the id is claimed by an INSERT that
  loses on the primary key. Checking-then-acting would leave a window where two
  concurrent deliveries both pass the check and both create a booking.
* **Trust the payload for nothing but ids.** The webhook body is thin by
  design: it says which revision changed, and the detail is pulled back from
  Channex over an authenticated call. That is what stops a forged webhook
  inventing a booking — the worst it can do is make us fetch something.
* **Answer 200 even when refusing to act.** A retry of something deliberately
  skipped is a queue that never drains. Refusal is for a bad secret, nothing
  else.

The one failure that needs a person is an unmapped room: the booking exists at
the OTA and does not exist here, and no amount of retrying fixes it. It is
recorded as ``unmapped``, left unacknowledged so Channex keeps offering it, and
is the thing a distribution screen should be shouting about.
"""

from __future__ import annotations

import hmac
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal

import httpx
from chirala_common.db import bind_tenant_context, system_context
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .inventory import InventoryShortage, create_hold
from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("channel")

channel_router = APIRouter(prefix="/channels", tags=["channels"],
                           route_class=TransactionalRoute)

#: Events that mean a booking changed. Channex sends others; they are recorded
#: and ignored rather than dropped silently, so "did you get our webhook" has
#: an answer.
HANDLED = {"booking", "booking_new", "booking_modification",
           "booking_cancellation"}


def _client() -> httpx.Client:
    """An authenticated client for pulling and acknowledging revisions."""
    return httpx.Client(
        base_url=settings.channex_api_url.rstrip("/"),
        headers={"user-api-key": settings.channex_api_key,
                 "Content-Type": "application/json"},
        timeout=20.0,
    )


@channel_router.post("/channex/webhook", status_code=status.HTTP_200_OK)
async def channex_webhook(
    request: Request,
    x_channex_webhook_secret: str | None = Header(default=None),
    db: Session = Depends(get_session),
):
    """Receive one Channex event.

    The secret is compared with ``compare_digest`` rather than ``==``. A plain
    comparison stops at the first differing byte, and how long that took says
    how much of a guess was right — enough, over many attempts, to find the
    whole secret.
    """
    secret = settings.channex_webhook_secret
    if not secret:
        # Refusing is the safe answer. Accepting unsigned events "until the
        # secret is configured" is how a test endpoint ends up in production
        # creating bookings for anyone who finds the URL.
        log.error("channex webhook received but no secret is configured")
        raise HTTPException(
            status_code=503,
            detail="The channel integration is not configured.")
    if not x_channex_webhook_secret or not hmac.compare_digest(
            x_channex_webhook_secret, secret):
        log.warning("channex webhook rejected: bad secret")
        raise HTTPException(status_code=401, detail="Unauthorised.")

    try:
        event = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Malformed body.") from None

    event_type = str(event.get("event") or "")
    payload = event.get("payload") or {}
    # `revision_id` is the documented field. The others are accepted because
    # naming drifts between a provider's docs and its wire format, and the cost
    # of being wrong here is every booking silently doing nothing.
    revision_id = str(payload.get("revision_id")
                      or payload.get("booking_revision_id")
                      or event.get("revision_id") or "")

    # Channex's "Send test message" button sends this. Answering "ignored" is
    # accurate and reads like a failure, which is a poor thing to show somebody
    # at the exact moment they are checking whether the wiring works. It says
    # so plainly instead — the secret was accepted, and that is what the test
    # is really asking.
    if event_type == "test":
        return {"status": "ok",
                "detail": "Webhook reached the PMS and the secret matched. "
                          "Booking events will be processed."}
    if event_type not in HANDLED:
        # Recorded nowhere and acted on not at all, but answered 200: a
        # provider that gets an error retries, and retrying something
        # deliberately skipped is a queue that never drains.
        return {"status": "ignored", "event": event_type}
    if not revision_id:
        # Almost always one thing: the webhook was created in Channex with
        # "Send Data" off, so the body is metadata only and carries no
        # revision to fetch. Said plainly, because the symptom otherwise is
        # bookings that never arrive and a webhook that looks healthy.
        log.error(
            "channex %s carried no revision_id. Enable 'Send Data' on the "
            "webhook in Channex, or the payload has no booking to fetch.",
            event_type)
        return {"status": "no_revision_id", "event": event_type,
                "hint": "Enable 'Send Data' on the Channex webhook."}

    return _ingest(db, revision_id, event_type)


def _ingest(db: Session, revision_id: str, event_type: str) -> dict:
    """Claim, fetch, apply, acknowledge."""
    # Which hotel a channel booking belongs to is not known until its property
    # id is matched to a link, so the claim and that match read across
    # tenants. The moment the property is known, the transaction narrows.
    system_context(db, reason="channel webhook: claim revision and match property")
    claimed = db.execute(
        text(
            """
            INSERT INTO distribution.channel_booking_events
                (revision_id, provider, event_type, outcome)
            VALUES (:rid, 'channex', :etype, 'claimed')
            ON CONFLICT (revision_id) DO NOTHING
            RETURNING revision_id
            """
        ),
        {"rid": revision_id, "etype": event_type},
    ).first()
    if claimed is None:
        # Seen before. 200, so Channex stops retrying something already dealt
        # with.
        return {"status": "duplicate", "revision_id": revision_id}

    if not settings.channex_api_key:
        _finish(db, revision_id, "failed",
                detail="No CHANNEX_API_KEY configured; cannot fetch the "
                       "revision.")
        log.error("channex webhook for %s but no API key is configured",
                  revision_id)
        return {"status": "unconfigured", "revision_id": revision_id}

    try:
        with _client() as c:
            resp = c.get(f"/booking_revisions/{revision_id}")
    except httpx.HTTPError as exc:
        # Left unacknowledged on purpose: Channex will offer it again, which is
        # exactly what should happen when the fetch failed for a transient
        # reason.
        _finish(db, revision_id, "failed", detail=f"Fetch failed: {exc}")
        log.error("could not fetch channex revision %s: %s", revision_id, exc)
        return {"status": "deferred", "revision_id": revision_id}

    if resp.status_code >= 400:
        _finish(db, revision_id, "failed",
                detail=f"Fetch returned {resp.status_code}: {resp.text[:300]}")
        log.error("channex revision %s fetch: %s", revision_id,
                  resp.status_code)
        return {"status": "deferred", "revision_id": revision_id}

    attrs = ((resp.json() or {}).get("data") or {}).get("attributes") or {}
    return _apply(db, revision_id, attrs)


def _apply(db: Session, revision_id: str, a: dict) -> dict:
    """Turn one revision into a reservation, or record why it could not be."""
    status_ = str(a.get("status") or "").lower()
    channex_property = str(a.get("property_id") or "")
    rooms = a.get("rooms") or []

    # Routed by the channel manager's property id, which belongs to the
    # property rather than to any one OTA. A booking from Agoda and one from
    # Booking.com arrive carrying the same id, and both belong to the same
    # hotel — which is the whole reason this lives on a link and not on a
    # partner connection.
    conn = db.execute(
        text(
            """
            SELECT l.id, l.organization_id, l.property_id
            FROM distribution.channel_manager_links l
            WHERE l.external_property_id = :ext
            """
        ),
        {"ext": channex_property},
    ).mappings().first()

    db.execute(
        text(
            """
            UPDATE distribution.channel_booking_events
               SET booking_id = :bid, ota_reservation_code = :code,
                   ota_name = :ota, status = :st, payload = CAST(:p AS json),
                   organization_id = :org, property_id = :prop,
                   updated_at = now()
             WHERE revision_id = :rid
            """
        ),
        {"rid": revision_id, "bid": str(a.get("booking_id") or ""),
         "code": str(a.get("ota_reservation_code") or ""),
         "ota": str(a.get("ota_name") or ""), "st": status_,
         "p": _json(a),
         "org": conn["organization_id"] if conn else None,
         "prop": conn["property_id"] if conn else None},
    )

    if conn is None:
        # A booking for a property nobody has connected here. Recorded loudly
        # and left unacknowledged: it is somebody's booking, and the only way
        # anyone finds it is if we kept it.
        _finish(db, revision_id, "unmapped",
                detail=f"No property is linked to channel manager property "
                       f"'{channex_property}'.")
        log.error("channex booking for unknown property %s", channex_property)
        return {"status": "unmapped", "revision_id": revision_id}

    # From here every read and write -- the guest, the hold, the reservation
    # -- belongs to this property's tenant and no other.
    bind_tenant_context(db, organization_id=conn["organization_id"],
                        property_id=conn["property_id"], is_service=True)

    if status_ == "cancelled":
        # Cancelling touches inventory and a folio, which this service does
        # elsewhere and should not reimplement at the edge. Recorded for a
        # person until that path is wired through.
        _finish(db, revision_id, "cancelled",
                detail="Cancellation received; needs processing by the desk.")
        return {"status": "cancelled", "revision_id": revision_id}

    if not rooms:
        _finish(db, revision_id, "failed", detail="Revision carried no rooms.")
        return {"status": "failed", "revision_id": revision_id}

    # Resolve every room before creating anything: a booking half-created
    # because its second room was unmapped is worse than one not created.
    lines = []
    for r in rooms:
        external = str(r.get("room_type_id") or "")
        local = db.execute(
            text("SELECT room_type_id FROM distribution.channel_room_mappings "
                 "WHERE link_id = :c AND external_id = :e"),
            {"c": conn["id"], "e": external},
        ).scalar()
        if local is None:
            _finish(db, revision_id, "unmapped",
                    detail=f"Channel room '{external}' is not mapped to a "
                           f"room type. Map it, then replay this booking.")
            log.error("channex booking %s uses unmapped room %s",
                      revision_id, external)
            return {"status": "unmapped", "revision_id": revision_id}
        occ = r.get("occupancy") or {}
        lines.append({
            "room_type_id": local,
            "arrival": _date(r.get("checkin_date") or a.get("arrival_date")),
            "departure": _date(r.get("checkout_date")
                               or a.get("departure_date")),
            "adults": int(occ.get("adults") or 1),
            "children": int(occ.get("children") or 0),
            "rate": _money(r.get("amount")),
        })

    guest_id = _guest(db, conn["organization_id"], a.get("customer") or {})

    try:
        held = create_hold(
            db,
            organization_id=conn["organization_id"],
            property_id=conn["property_id"],
            room_type_id=lines[0]["room_type_id"],
            arrival_date=lines[0]["arrival"],
            departure_date=lines[0]["departure"],
            units=len(lines),
            adults=lines[0]["adults"],
            children=lines[0]["children"],
            guest_id=guest_id,
            # The channel is how it arrived. 'ota' already exists in the
            # source vocabulary; inventing 'channex' would split OTA business
            # across two labels in every revenue report.
            source="ota",
            reference=str(a.get("ota_reservation_code") or ""),
            # The revision id, so a replay of the same booking is refused by
            # the hold's own idempotency rather than by luck.
            idempotency_key=f"channex-{a.get('booking_id') or ''}",
            hold_ttl_minutes=settings.hold_ttl_minutes,
            overbooking_allowance=0,
        )
    except InventoryShortage:
        # The OTA sold something we do not have. Unacknowledged, so it keeps
        # being offered, and loud: somebody has to close the channel or find a
        # room.
        _finish(db, revision_id, "no_inventory",
                detail="No inventory for those dates. The OTA has sold a room "
                       "this property does not have.")
        log.error("channex booking %s oversold: no inventory", revision_id)
        return {"status": "no_inventory", "revision_id": revision_id}

    # The rate the channel sold at, so the folio bills what the guest was told.
    for i, line in enumerate(lines):
        if line["rate"] is None:
            continue
        nights = max(1, (line["departure"] - line["arrival"]).days)
        db.execute(
            text("UPDATE booking.reservation_units SET nightly_rate = :r "
                 "WHERE reservation_id = :res AND line_index = :i"),
            {"r": (line["rate"] / nights).quantize(Decimal("0.01")),
             "res": held.reservation_id, "i": i},
        )

    db.execute(
        text("UPDATE booking.reservations SET status = 'confirmed' "
             "WHERE id = :r"),
        {"r": held.reservation_id},
    )
    db.execute(
        text("UPDATE booking.booking_holds SET status = 'converted' "
             "WHERE reservation_id = :r"),
        {"r": held.reservation_id},
    )

    _finish(db, revision_id, "created", reservation_id=held.reservation_id,
            detail=f"Created {held.number}.")
    _ack(db, revision_id)
    log.info("channex booking %s created reservation %s", revision_id,
             held.number)
    return {"status": "created", "revision_id": revision_id,
            "reservation_number": held.number}


def _ack(db: Session, revision_id: str) -> None:
    """Tell Channex we have it, so it stops offering it.

    Deliberately last, and only after the booking exists. Acknowledging first
    would be faster and would lose a booking the moment anything downstream
    failed — Channex would never offer it again, and nothing here would have
    it either.
    """
    if not settings.channex_api_key:
        return
    try:
        with _client() as c:
            resp = c.post(f"/booking_revisions/{revision_id}/ack")
        if resp.status_code < 400:
            db.execute(
                text("UPDATE distribution.channel_booking_events "
                     "SET acknowledged = true, updated_at = now() "
                     "WHERE revision_id = :r"),
                {"r": revision_id},
            )
        else:
            log.error("channex ack for %s returned %s", revision_id,
                      resp.status_code)
    except httpx.HTTPError as exc:
        # Not fatal. An unacknowledged booking is offered again, and the
        # revision id makes the second delivery a duplicate rather than a
        # second booking.
        log.error("channex ack for %s failed: %s", revision_id, exc)


def _finish(db: Session, revision_id: str, outcome: str, *,
            detail: str | None = None, reservation_id=None) -> None:
    db.execute(
        text("UPDATE distribution.channel_booking_events "
             "SET outcome = :o, detail = :d, reservation_id = :res, "
             "updated_at = now() WHERE revision_id = :r"),
        {"o": outcome, "d": detail, "res": reservation_id, "r": revision_id},
    )


def _guest(db: Session, organization_id, customer: dict):
    """Find this guest, or record them.

    Matched on email within the organisation, the same rule the booking engine
    uses — a returning guest should not become a new person every time.
    """
    email = str(customer.get("mail") or "").strip().lower()
    name = " ".join(x for x in (customer.get("name"),
                                customer.get("surname")) if x).strip()
    if not name:
        name = "Channel guest"
    if email:
        found = db.execute(
            text("SELECT id FROM engagement.guests "
                 "WHERE organization_id = :o AND lower(email) = :e LIMIT 1"),
            {"o": organization_id, "e": email},
        ).scalar()
        if found:
            return found
    gid = uuid.uuid4()
    db.execute(
        text("INSERT INTO engagement.guests "
             "(id, organization_id, full_name, email, phone) "
             "VALUES (:id, :o, :n, :e, :p)"),
        {"id": gid, "o": organization_id, "n": name[:200],
         "e": email or None,
         "p": str(customer.get("phone") or "").strip()[:40] or None},
    )
    return gid


def _date(v) -> date:
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def _money(v):
    try:
        return Decimal(str(v)) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def _json(v) -> str:
    import json
    return json.dumps(v)
