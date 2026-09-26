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
from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.db import bind_tenant_context, system_context
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .change_routes import CancelIn, ModifyIn, cancel_reservation, modify_reservation
from .database import get_session
from .flow import confirm_reservation
from .inventory import HoldLine, InventoryShortage, create_hold
from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("channel")

_get_caller, _require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

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
def channex_webhook(
    event: dict | None = Body(default=None),
    x_channex_webhook_secret: str | None = Header(default=None),
    db: Session = Depends(get_session),
):
    """Receive one Channex event.

    A plain ``def``, so FastAPI runs it in a worker thread: everything below
    is blocking database and HTTP work, and as an ``async def`` it stalled
    every other request this service answers for as long as Channex took.

    The secret is compared with ``compare_digest`` rather than ``==``. A plain
    comparison stops at the first differing byte, and how long that took says
    how much of a guess was right — enough, over many attempts, to find the
    whole secret. Compared as bytes, so a header with non-ASCII characters is
    refused rather than raising.
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
            x_channex_webhook_secret.encode(), secret.encode()):
        log.warning("channex webhook rejected: bad secret")
        raise HTTPException(status_code=401, detail="Unauthorised.")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Malformed body.")

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
    # at the exact moment they are checking whether the wiring works.
    if event_type == "test":
        return {"status": "ok",
                "detail": "Webhook reached the PMS and the secret matched. "
                          "Booking events will be processed."}
    if event_type not in HANDLED:
        # Acted on not at all, but answered 200: a provider that gets an error
        # retries, and retrying something deliberately skipped is a queue that
        # never drains.
        return {"status": "ignored", "event": event_type}
    if not revision_id:
        # Almost always one thing: the webhook was created in Channex with
        # "Send Data" off, so the body is metadata only and carries no
        # revision to fetch.
        log.error(
            "channex %s carried no revision_id. Enable 'Send Data' on the "
            "webhook in Channex, or the payload has no booking to fetch.",
            event_type)
        return {"status": "no_revision_id", "event": event_type,
                "hint": "Enable 'Send Data' on the Channex webhook."}

    result = ingest(db, revision_id, event_type)
    if result["status"] == "deferred":
        # The fetch failed for a reason that may pass. Anything but a 2xx
        # makes Channex deliver again, and the claim is retryable, so the
        # next delivery is processed rather than dismissed as a duplicate.
        # A JSONResponse (not an exception) so the record of the attempt is
        # still committed.
        return JSONResponse(result, status_code=503)
    return result


#: Outcomes a later delivery, the feed poll or a replay may try again. A
#: booking the channel manager holds and this PMS does not must never be
#: settled by a record of having failed once.
RETRYABLE = ("claimed", "failed", "unmapped", "no_inventory")


def ingest(db: Session, revision_id: str, event_type: str, *,
           replay: bool = False) -> dict:
    """Claim, fetch, apply, acknowledge."""
    # Which hotel a channel booking belongs to is not known until its property
    # id is matched to a link, so the claim and that match read across
    # tenants. The moment the property is known, the transaction narrows.
    system_context(db, reason="channel webhook: claim revision and match property")
    # A revision seen before is claimed again only if it never succeeded.
    # The old claim was an INSERT that lost on the primary key, which made a
    # fetch that failed once -- or an unmapped room fixed an hour later -- a
    # booking lost for good: every redelivery was answered "duplicate".
    claimed = db.execute(
        text(
            """
            INSERT INTO distribution.channel_booking_events
                (revision_id, provider, event_type, outcome)
            VALUES (:rid, 'channex', :etype, 'claimed')
            ON CONFLICT (revision_id) DO UPDATE
               SET outcome = 'claimed', updated_at = now()
             WHERE distribution.channel_booking_events.outcome
                   = ANY(CAST(:retry AS text[]))
               AND (:replay OR distribution.channel_booking_events.updated_at
                               < now() - interval '5 seconds'
                    OR distribution.channel_booking_events.outcome <> 'claimed')
            RETURNING revision_id
            """
        ),
        {"rid": revision_id, "etype": event_type, "retry": list(RETRYABLE),
         "replay": replay},
    ).first()
    if claimed is None:
        # Seen and dealt with. 200, so Channex stops retrying.
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


def _channel_caller(org) -> Caller:
    """The platform acting for this tenant, for the desk's own change paths.

    A cancellation or a modification made at the OTA is a fact the hotel has
    to honour, not a request to approve, so it goes through the same code the
    desk uses -- inventory, folio, audit and all -- as a service caller of
    this organisation and no other.
    """
    return Caller(subject="channex", user_id=None, organization_id=org,
                  is_service=True)


def _existing(db: Session, booking_id: str):
    """The reservation an earlier revision of this OTA booking created."""
    if not booking_id:
        return None
    return db.execute(
        text(
            """
            SELECT e.reservation_id, r.status, r.number
            FROM distribution.channel_booking_events e
            JOIN booking.reservations r ON r.id = e.reservation_id
            WHERE e.booking_id = :b AND e.reservation_id IS NOT NULL
            ORDER BY e.received_at DESC
            LIMIT 1
            """
        ),
        {"b": booking_id},
    ).mappings().first()


def _partner(db: Session, organization_id, ota_name: str):
    """The Channel Partners entry this OTA booking belongs to.

    Without it the booking counted for nobody: the partner list showed no
    bookings and no revenue, and the commission report had nothing to charge.
    Matched on name or code, the way the partner registry derives codes.
    """
    name = (ota_name or "").strip()
    if not name:
        return None
    return db.execute(
        text(
            """
            SELECT id FROM engagement.booking_attributes
            WHERE organization_id = :o AND kind = 'business_source'
              AND (lower(name) = lower(:n)
                   OR upper(code) = upper(regexp_replace(:n, '[^A-Za-z0-9]', '', 'g')))
            ORDER BY (status = 'active') DESC
            LIMIT 1
            """
        ),
        {"o": organization_id, "n": name},
    ).scalar()


def _lines(db: Session, link_id, a: dict, rooms: list[dict]):
    """Resolve every room before creating anything.

    A booking half-created because its second room was unmapped is worse
    than one not created. Returns (lines, unmapped external id or None).
    """
    lines = []
    for r in rooms:
        external = str(r.get("room_type_id") or "")
        local = db.execute(
            text("SELECT room_type_id FROM distribution.channel_room_mappings "
                 "WHERE link_id = :c AND external_id = :e"),
            {"c": link_id, "e": external},
        ).scalar()
        if local is None:
            return None, external
        occ = r.get("occupancy") or {}
        arrival = _date(r.get("checkin_date") or a.get("arrival_date"))
        departure = _date(r.get("checkout_date") or a.get("departure_date"))
        amount = _money(r.get("amount"))
        nights = max(1, (departure - arrival).days)
        lines.append({
            "room_type_id": local, "arrival": arrival, "departure": departure,
            "adults": int(occ.get("adults") or 1),
            "children": int(occ.get("children") or 0),
            "nightly": (amount / nights).quantize(Decimal("0.01"))
            if amount is not None else None,
        })
    return lines, None


def _create(db: Session, conn, a: dict, lines: list[dict], guest_id):
    """One reservation per set of stay dates, one unit per room.

    Each room keeps its own type, occupancy and price. The old path booked
    every room as a copy of the first -- a suite and a deluxe arrived as two
    deluxe rooms, and the suite's inventory was never taken.
    """
    groups: dict[tuple, list[dict]] = {}
    for line in lines:
        groups.setdefault((line["arrival"], line["departure"]), []).append(line)
    business_source = _partner(db, conn["organization_id"], a.get("ota_name"))
    made = []
    for n, ((arrival, departure), group) in enumerate(sorted(groups.items())):
        held = create_hold(
            db,
            organization_id=conn["organization_id"],
            property_id=conn["property_id"],
            arrival_date=arrival,
            departure_date=departure,
            lines=[HoldLine(room_type_id=l["room_type_id"], units=1,
                            adults=l["adults"], children=l["children"],
                            nightly_rate=l["nightly"]) for l in group],
            guest_id=guest_id,
            # 'ota' already exists in the source vocabulary; inventing
            # 'channex' would split OTA business across two labels in every
            # revenue report.
            source="ota",
            business_source_id=business_source,
            reference=str(a.get("ota_reservation_code") or ""),
            # The booking id, so a replay of the same booking is refused by
            # the hold's own idempotency rather than by luck.
            idempotency_key=f"channex-{a.get('booking_id') or ''}"
                            + (f"-{n}" if n else ""),
            hold_ttl_minutes=settings.hold_ttl_minutes,
            overbooking_allowance=0,
        )
        # Through the booking flow, not a status update: confirming moves the
        # rooms from held to reserved. Setting the status directly left every
        # OTA booking counted as a hold for ever -- off sale even after it was
        # cancelled.
        confirm_reservation(db, reservation_id=held.reservation_id)
        made.append(held)
    return made


def _apply(db: Session, revision_id: str, a: dict) -> dict:
    """Turn one revision into a reservation, a change, or a cancellation."""
    status_ = str(a.get("status") or "").lower()
    channex_property = str(a.get("property_id") or "")
    rooms = a.get("rooms") or []

    # Routed by the channel manager's property id, which belongs to the
    # property rather than to any one OTA.
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
        # A booking for a property nobody has connected here. Kept, loudly,
        # and retryable: it is somebody's booking.
        _finish(db, revision_id, "unmapped",
                detail=f"No property is linked to channel manager property "
                       f"'{channex_property}'.")
        log.error("channex booking for unknown property %s", channex_property)
        return {"status": "unmapped", "revision_id": revision_id}

    # From here every read and write belongs to this property's tenant.
    bind_tenant_context(db, organization_id=conn["organization_id"],
                        property_id=conn["property_id"], is_service=True)
    caller = _channel_caller(conn["organization_id"])
    existing = _existing(db, str(a.get("booking_id") or ""))
    code = str(a.get("ota_reservation_code") or "")
    ota = str(a.get("ota_name") or "the OTA")

    # ---- cancellation ------------------------------------------------------
    if status_ == "cancelled":
        if existing is None:
            _finish(db, revision_id, "cancelled",
                    detail="Cancellation for a booking this PMS never had.")
        elif existing["status"] == "cancelled":
            _finish(db, revision_id, "cancelled",
                    reservation_id=existing["reservation_id"],
                    detail=f"{existing['number']} was already cancelled.")
        else:
            cancel_reservation(
                existing["reservation_id"], conn["property_id"],
                CancelIn(reason="guest_request",
                         notes=f"Cancelled on {ota} ({code}).",
                         # The OTA's own policy settles any fee with the
                         # guest; charging it again here would bill twice.
                         waive_penalty=True),
                caller=caller, db=db)
            _finish(db, revision_id, "cancelled",
                    reservation_id=existing["reservation_id"],
                    detail=f"Cancelled {existing['number']}; rooms released.")
        _ack(db, revision_id)
        return {"status": "cancelled", "revision_id": revision_id}

    if not rooms:
        _finish(db, revision_id, "failed", detail="Revision carried no rooms.")
        return {"status": "failed", "revision_id": revision_id}

    lines, unmapped = _lines(db, conn["id"], a, rooms)
    if unmapped is not None:
        _finish(db, revision_id, "unmapped",
                detail=f"Channel room '{unmapped}' is not mapped to a room "
                       f"type. Map it, then replay this booking.")
        log.error("channex booking %s uses unmapped room %s",
                  revision_id, unmapped)
        return {"status": "unmapped", "revision_id": revision_id}

    # ---- modification ------------------------------------------------------
    if existing is not None:
        if existing["status"] == "cancelled":
            # Never resurrect: a booking the desk or the OTA cancelled stays
            # cancelled until a person decides otherwise.
            _finish(db, revision_id, "failed",
                    reservation_id=existing["reservation_id"],
                    detail=f"Modification for {existing['number']}, which is "
                           f"cancelled here. Not applied; check with {ota}.")
            return {"status": "failed", "revision_id": revision_id}
        units = db.execute(
            text("SELECT id FROM booking.reservation_units "
                 "WHERE reservation_id = :r AND status <> 'cancelled' "
                 "ORDER BY line_index, id"),
            {"r": existing["reservation_id"]},
        ).all()
        same_shape = (len(units) == len(lines)
                      and len({(l["arrival"], l["departure"], l["room_type_id"])
                               for l in lines}) == 1)
        if same_shape:
            first = lines[0]
            try:
                modify_reservation(
                    existing["reservation_id"], conn["property_id"],
                    ModifyIn(arrival_date=first["arrival"],
                             departure_date=first["departure"],
                             room_type_id=first["room_type_id"],
                             adults=first["adults"],
                             children=first["children"],
                             reason="guest_request",
                             notes=f"Changed on {ota} ({code}).",
                             # The OTA sold the new stay at its own price,
                             # written below; a desk re-quote does not apply.
                             charge_difference=False),
                    caller=caller, db=db)
            except HTTPException as exc:
                _finish(db, revision_id, "no_inventory"
                        if exc.status_code == 409 else "failed",
                        reservation_id=existing["reservation_id"],
                        detail=f"Modification not applied: {exc.detail}")
                return {"status": "failed", "revision_id": revision_id}
            for (uid,), line in zip(units, lines):
                if line["nightly"] is not None:
                    db.execute(
                        text("UPDATE booking.reservation_units "
                             "SET nightly_rate = :r WHERE id = :u"),
                        {"r": line["nightly"], "u": uid})
            _finish(db, revision_id, "updated",
                    reservation_id=existing["reservation_id"],
                    detail=f"Updated {existing['number']}.")
            _ack(db, revision_id)
            return {"status": "modified", "revision_id": revision_id,
                    "reservation_number": existing["number"]}
        # Rooms added, removed or split across dates: the desk's change flow
        # moves one stay, not a reshaped booking. Replace it -- cancel the old
        # (no fee) and book the new -- in this same transaction, so a failure
        # leaves the original untouched.
        guest_id = _guest(db, conn["organization_id"], a.get("customer") or {})
        savepoint = db.begin_nested()
        try:
            cancel_reservation(
                existing["reservation_id"], conn["property_id"],
                CancelIn(reason="date_change",
                         notes=f"Replaced by a change on {ota} ({code}).",
                         waive_penalty=True),
                caller=caller, db=db)
            made = _create(db, conn, {**a, "booking_id":
                                      f"{a.get('booking_id')}-{revision_id[:8]}"},
                           lines, guest_id)
            savepoint.commit()
        except InventoryShortage:
            savepoint.rollback()
            _finish(db, revision_id, "no_inventory",
                    reservation_id=existing["reservation_id"],
                    detail=f"Change not applied: no inventory for the new "
                           f"stay. {existing['number']} is unchanged.")
            return {"status": "no_inventory", "revision_id": revision_id}
        _finish(db, revision_id, "updated", reservation_id=made[0].reservation_id,
                detail=f"Replaced {existing['number']} with "
                       f"{', '.join(h.number for h in made)}.")
        _ack(db, revision_id)
        return {"status": "modified", "revision_id": revision_id,
                "reservation_number": made[0].number}

    # ---- new booking -------------------------------------------------------
    guest_id = _guest(db, conn["organization_id"], a.get("customer") or {})
    try:
        made = _create(db, conn, a, lines, guest_id)
    except InventoryShortage:
        # The OTA sold something we do not have. Retryable, and loud: somebody
        # has to close the channel or find a room, then replay.
        _finish(db, revision_id, "no_inventory",
                detail="No inventory for those dates. The OTA has sold a room "
                       "this property does not have.")
        log.error("channex booking %s oversold: no inventory", revision_id)
        return {"status": "no_inventory", "revision_id": revision_id}

    numbers = ", ".join(h.number for h in made)
    _finish(db, revision_id, "created", reservation_id=made[0].reservation_id,
            detail=f"Created {numbers}.")
    _ack(db, revision_id)
    log.info("channex booking %s created reservation %s", revision_id, numbers)
    return {"status": "created", "revision_id": revision_id,
            "reservation_number": made[0].number}


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
    except Exception:
        return None


def _json(v) -> str:
    import json
    return json.dumps(v)


# ------------------------------------------------------------------- desk --
class BookingEventOut(BaseModel):
    revision_id: str
    event_type: str | None = None
    outcome: str
    status: str | None = None
    ota_name: str | None = None
    ota_reservation_code: str | None = None
    reservation_id: uuid.UUID | None = None
    reservation_number: str | None = None
    detail: str | None = None
    acknowledged: bool = False
    created_at: datetime
    updated_at: datetime | None = None
    can_replay: bool = False


@channel_router.get("/events", response_model=list[BookingEventOut])
def list_booking_events(
    property_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    """What the channel manager has delivered for this property, newest first.

    The one place a booking that exists at the OTA and not here -- unmapped,
    oversold, failed -- is visible, with a way to try it again.
    """
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT e.revision_id, e.event_type, e.outcome, e.status,
                   e.ota_name, e.ota_reservation_code, e.reservation_id,
                   r.number AS reservation_number, e.detail,
                   e.acknowledged, e.received_at AS created_at, e.updated_at
            FROM distribution.channel_booking_events e
            LEFT JOIN booking.reservations r ON r.id = e.reservation_id
            WHERE e.property_id = :p
            ORDER BY e.received_at DESC
            LIMIT :n
            """
        ),
        {"p": property_id, "n": limit},
    ).mappings().all()
    return [BookingEventOut(**r, can_replay=r["outcome"] in RETRYABLE)
            for r in rows]


@channel_router.post("/events/{revision_id}/replay")
def replay_booking_event(
    revision_id: str,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Try a booking again -- after mapping its room, or freeing inventory."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text("SELECT outcome FROM distribution.channel_booking_events "
             "WHERE revision_id = :r AND property_id = :p"),
        {"r": revision_id, "p": property_id},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such booking event.")
    if row[0] not in RETRYABLE:
        raise HTTPException(status_code=409,
                            detail=f"Already {row[0]}; nothing to replay.")
    record_audit(db, action="channel_booking.replayed",
                 entity_type="channel_booking_event", entity_id=revision_id,
                 organization_id=caller.organization_id,
                 property_id=property_id, actor_subject=caller.subject)
    return ingest(db, revision_id, "booking", replay=True)


def poll_feed(db_factory) -> int:
    """Pick up every revision the channel manager still holds unacknowledged.

    The safety net under the webhook: a delivery that never arrived, a
    deployment that was down, a webhook pointed at an old address. Anything
    Channex has not had an acknowledgement for is offered here and ingested
    like a webhook would -- the claim makes a second arrival harmless.
    """
    if not settings.channex_api_key:
        return 0
    try:
        with _client() as c:
            resp = c.get("/booking_revisions/feed")
        if resp.status_code >= 400:
            log.warning("channex feed: %s", resp.status_code)
            return 0
        pending = (resp.json() or {}).get("data") or []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("channex feed failed: %s", exc)
        return 0
    done = 0
    for item in pending:
        rid = str(item.get("id") or (item.get("attributes") or {}).get("id") or "")
        if not rid:
            continue
        with db_factory() as db:
            try:
                res = ingest(db, rid, "booking")
                db.commit()
                done += res["status"] not in ("duplicate",)
            except Exception:
                db.rollback()
                log.exception("channex feed: revision %s failed", rid)
    return done
