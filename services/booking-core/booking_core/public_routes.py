"""What a guest can see without logging in.

This is the read half of a booking engine: given a property and some dates,
what is on sale and what does it cost. Everything else in this service answers
to a signed-in member of staff; this answers to the internet.

That difference drives every decision here:

* **The property comes from the URL, never from the caller.** Every other
  endpoint resolves the tenant from the session. There is no session here, so
  the only safe source is the path — a public endpoint that accepted a
  ``property_id`` parameter would be the same hole that once let four
  unauthenticated endpoints return every tenant's data.
* **Internal ids stay internal.** Room types are addressed by their code. A
  UUID is not a secret, but nothing outside needs one, and anything published
  becomes something that has to keep working.
* **Only what a guest is meant to see.** Active property, active room types,
  guest-visible amenities. Not "not on sale (2/3)" or a blocked reason — those
  describe how the inventory is configured, which is nobody's business but the
  hotel's. A night that cannot be sold is simply not for sale.
* **A property is not on sale until somebody says so.** Every lookup here
  requires the ``booking_engine`` module entitlement, and a property without it
  answers exactly as one that does not exist. Being reachable by a six-digit
  code is not consent to being sold online: before this gate existed, creating
  a property published it, and anyone who knew the code could hold its rooms.

Rate limiting is in place (per caller, per window). **Bot protection is not.**
That still matters here, because holding a room needs no payment and no
account: a caller who walks the codes can take a property's inventory off sale
for the length of a hold and keep doing it. The entitlement above limits that
to properties that chose to sell online; it does not protect the ones that did.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from chirala_common.db import bind_tenant_context, property_code_context
from chirala_common.routing import TransactionalRoute

from .database import get_session
from .inventory import InventoryShortage, create_hold
from .ratelimit import rate_limit
from .branding import theme
from .rooms_routes import photo_url
from .settings import settings

# The limiter hangs off the router, not individual endpoints: anything added
# to this prefix later is public by definition and inherits the protection
# rather than needing somebody to remember it.
# ``route_class`` for the same reason as every other router in this service,
# and more so here: /book and /pay are the two writes a guest sees the result
# of. Without it the commit happens in dependency teardown, after the response
# has gone -- so a commit that fails leaves the guest holding a confirmation
# number for a reservation that does not exist. See chirala_common.routing.
public_router = APIRouter(
    prefix="/public", tags=["public"], dependencies=[Depends(rate_limit)],
    route_class=TransactionalRoute)

#: Nobody plans a stay longer than this through a booking engine, and an
#: unbounded range is an invitation to ask for a decade of rates in one call.
MAX_NIGHTS = 30

log = logging.getLogger("uvicorn.error").getChild("public")


class PublicPhoto(BaseModel):
    url: str
    caption: str | None = None


class PublicRoomType(BaseModel):
    code: str
    name: str
    description: str | None = None
    max_occupancy: int | None = None
    bed_setup: str | None = None
    size_sqft: int | None = None
    room_view: str | None = None
    photos: list[PublicPhoto] = Field(default_factory=list)
    amenities: list[str] = Field(default_factory=list)
    #: Rooms of this type that can be sold for every night of the stay.
    available: int
    #: The stay, priced. Nightly is the average — a rate calendar can differ
    #: night to night, and quoting one night's price for a week would be a
    #: number the guest is never charged.
    nightly_from: Decimal
    total: Decimal


class PublicBranding(BaseModel):
    """How this hotel's booking page should look.

    Always present, always the same shape -- an unbranded property returns
    nulls rather than nothing, so the page never has to decide what a missing
    key means. `ink_on_brand` is computed from the brand colour, never chosen
    by the tenant: see branding.py.
    """

    brand_color: str | None = None
    ink_on_brand: str | None = None
    tagline: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None


class PublicProperty(BaseModel):
    code: str
    name: str
    currency: str
    checkin_time: str | None = None
    checkout_time: str | None = None
    city: str | None = None
    country: str | None = None
    branding: PublicBranding = Field(default_factory=PublicBranding)


class AvailabilityOut(BaseModel):
    property: PublicProperty
    arrival: date
    departure: date
    nights: int
    adults: int
    children: int
    room_types: list[PublicRoomType]


def _local_today(tz_name: str | None) -> date:
    try:
        tz = ZoneInfo(tz_name or "Asia/Kolkata")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz).date()


#: The module code that puts a property on sale to the public.
#:
#: ``iam.property_modules`` has been the entitlement table since the first
#: migration ("Module entitlement per property") and had never been used. It is
#: the right home: whether a tenant *has* the booking engine is a platform
#: decision, distinct from how they configure one they have.
BOOKING_ENGINE_MODULE = "booking_engine"

#: Appended to every public property lookup, so the entitlement is part of
#: *resolving* the property rather than a separate check.
#:
#: Written this way on purpose. A gate that lives in its own ``if`` is a gate
#: the next public endpoint forgets; folded into the lookup, there is no way to
#: obtain a property row without being entitled to sell it. No row means not
#: entitled -- so a newly created property is never silently on sale.
_ENTITLED = """
      AND EXISTS (SELECT 1 FROM iam.property_modules m
                   WHERE m.property_id = p.id
                     AND m.module_code = 'booking_engine'
                     AND m.enabled)
"""


#: Availability and price in one pass.
#:
#: A room type is sellable only if every night of the stay has an inventory row
#: *and* something left on it — hence the count as well as the minimum. A
#: missing night is not "zero available", it is a night the hotel never put on
#: sale, and treating the two the same is what stops a half-loaded calendar
#: from quietly selling rooms it has no record of.
_AVAILABILITY_SQL = """
    WITH nights AS (
        SELECT d::date AS stay_date
        FROM generate_series(CAST(:arr AS date),
                             CAST(:dep AS date) - 1, interval '1 day') AS d
    )
    SELECT rt.id, rt.code, rt.name, rt.description, rt.max_occupancy,
           rt.bed_setup, rt.size_sqft, rt.room_view,
           count(i.stay_date) AS nights_loaded,
           coalesce(min(i.physical_capacity - i.out_of_service - i.held_units
                        - i.reserved_units - i.allotment_units), 0) AS available,
           coalesce(sum(COALESCE(rc.rate, rt.base_rate, 0)), 0) AS total
    FROM property.room_types rt
    CROSS JOIN nights n
    LEFT JOIN booking.room_type_inventory_days i
           ON i.room_type_id = rt.id AND i.stay_date = n.stay_date
    LEFT JOIN property.rate_calendar_days rc
           ON rc.room_type_id = rt.id AND rc.stay_date = n.stay_date
    WHERE rt.property_id = :prop
      AND rt.status = 'active'
      AND (rt.max_occupancy IS NULL OR rt.max_occupancy >= :guests)
    GROUP BY rt.id
    ORDER BY 11, rt.name
"""


def _public_property(db, property_code: str) -> PublicProperty:
    """One property, as a guest may see it, with its branding.

    Shared by the landing lookup and the availability search so the two can
    never disagree about a hotel's name or colour -- and so the entitlement
    gate is applied once, in one place, rather than copied.
    """
    property_code_context(db, code=property_code)
    prop = db.execute(
        text(
            """
            SELECT p.id, p.organization_id, p.code, p.name, p.currency,
                   p.checkin_time, p.checkout_time, p.city, p.country
            FROM iam.properties p
            WHERE p.code = :c AND p.status = 'active'
            """ + _ENTITLED
        ),
        {"c": property_code},
    ).mappings().first()
    if prop is None:
        raise HTTPException(status_code=404, detail="No such property.")
    bind_tenant_context(db, organization_id=prop["organization_id"],
                        property_id=prop["id"])

    brand = db.execute(
        text("SELECT brand_color, tagline, logo_key, banner_key "
             "FROM property.booking_branding WHERE property_id = :p"),
        {"p": prop["id"]},
    ).mappings().first()
    palette = theme(brand["brand_color"] if brand else None)
    return PublicProperty(
        code=prop["code"], name=prop["name"],
        currency=prop["currency"] or "INR",
        checkin_time=prop["checkin_time"], checkout_time=prop["checkout_time"],
        city=prop["city"], country=prop["country"],
        branding=PublicBranding(
            brand_color=palette["brand_color"],
            ink_on_brand=palette["ink_on_brand"],
            tagline=brand["tagline"] if brand else None,
            logo_url=photo_url(None, brand["logo_key"], None) if brand else None,
            banner_url=(photo_url(None, brand["banner_key"], None)
                        if brand else None),
        ),
    )


@public_router.get("/{property_code}", response_model=PublicProperty)
def public_property(
    property_code: str,
    db: Session = Depends(get_session),
):
    """Who this booking page belongs to, before any dates are chosen.

    Without this the page could not know whose hotel it was until the guest
    had already searched, so the first thing anybody saw was the platform's
    own colours and the word "Book your stay" -- the one screen where looking
    like the hotel matters most. Branding that arrives on the second screen is
    not branding.

    Cheap on purpose: no dates, no inventory, no prices. It answers the one
    question the page has on load and nothing else.
    """
    return _public_property(db, property_code)


@public_router.get("/{property_code}/availability",
                   response_model=AvailabilityOut)
def availability(
    property_code: str,
    arrival: date = Query(...),
    departure: date = Query(...),
    adults: int = Query(2, ge=1, le=10),
    children: int = Query(0, ge=0, le=10),
    db: Session = Depends(get_session),
):
    """What is on sale at this property for these dates, and what it costs."""
    # Only the property this code names is readable until its tenant is bound.
    property_code_context(db, code=property_code)
    prop = db.execute(
        text(
            """
            SELECT p.id, p.organization_id, p.code, p.name, p.currency,
                   p.timezone, p.checkin_time, p.checkout_time, p.city, p.country
            FROM iam.properties p
            WHERE p.code = :c AND p.status = 'active'
            """ + _ENTITLED
        ),
        {"c": property_code},
    ).mappings().first()
    # The same answer for a property that does not exist and one that is not
    # taking bookings: a public endpoint should not be a directory of which
    # tenants exist.
    if prop is None:
        raise HTTPException(status_code=404, detail="No such property.")
    # A guest has no login; the property named in the URL decides whose
    # records this request may read, and nothing wider.
    bind_tenant_context(db, organization_id=prop["organization_id"],
                        property_id=prop["id"])

    today = _local_today(prop["timezone"])
    nights = (departure - arrival).days
    if nights < 1:
        raise HTTPException(
            status_code=422,
            detail="Departure must be at least one night after arrival.")
    if nights > MAX_NIGHTS:
        raise HTTPException(
            status_code=422,
            detail=f"Stays longer than {MAX_NIGHTS} nights cannot be booked "
                   f"online — please contact the property.")
    if arrival < today:
        raise HTTPException(
            status_code=422, detail="Arrival cannot be in the past.")
    horizon = today + timedelta(days=settings.inventory_horizon_days)
    if arrival > horizon:
        raise HTTPException(
            status_code=422,
            detail=f"The calendar is open to {horizon:%d %b %Y}.")

    rows = db.execute(
        text(_AVAILABILITY_SQL),
        {"prop": prop["id"], "arr": arrival, "dep": departure,
         "guests": adults + children},
    ).mappings().all()

    ids = [r["id"] for r in rows]
    photos: dict[uuid.UUID, list[PublicPhoto]] = {}
    amenities: dict[uuid.UUID, list[str]] = {}
    if ids:
        for r in db.execute(
            text(
                """
                SELECT room_type_id, url, storage_key, thumb_key, caption
                FROM property.room_type_photos
                WHERE room_type_id = ANY(:ids)
                ORDER BY is_primary DESC, sort_order, created_at
                """
            ),
            {"ids": ids},
        ).mappings():
            # An uploaded photo has only a storage key and is presigned; one
            # hosted elsewhere keeps its URL. Resolved by the same helper the
            # staff screens use, so a guest and the front desk never disagree
            # about which picture a room type has.
            # The thumbnail, not the original. This is a list of room types
            # on somebody's phone: the picture is drawn about a hundred pixels
            # wide, and the full-size upload is megabytes spent on detail the
            # box cannot show. A photo with no thumbnail falls back on its own.
            link = photo_url(r["url"], r["storage_key"], r["thumb_key"])
            if not link:
                # A photo the object store cannot serve is simply not offered.
                # A broken image on the booking page is worse than one fewer.
                continue
            photos.setdefault(r["room_type_id"], []).append(
                PublicPhoto(url=link, caption=r["caption"]))
        for r in db.execute(
            text(
                """
                SELECT rta.room_type_id, a.name
                FROM property.room_type_amenities rta
                JOIN property.amenities a ON a.id = rta.amenity_id
                WHERE rta.room_type_id = ANY(:ids)
                  AND a.status = 'active' AND a.guest_visible
                ORDER BY a.name
                """
            ),
            {"ids": ids},
        ).mappings():
            amenities.setdefault(r["room_type_id"], []).append(r["name"])

    out: list[PublicRoomType] = []
    for r in rows:
        # Every night must be loaded, or the stay is not sellable as a whole.
        sellable = int(r["available"]) if r["nights_loaded"] == nights else 0
        total = Decimal(str(r["total"]))
        if sellable < 1 or total <= 0:
            continue
        out.append(PublicRoomType(
            code=r["code"], name=r["name"], description=r["description"],
            max_occupancy=r["max_occupancy"], bed_setup=r["bed_setup"],
            size_sqft=r["size_sqft"], room_view=r["room_view"],
            photos=photos.get(r["id"], []),
            amenities=amenities.get(r["id"], []),
            available=sellable,
            nightly_from=(total / nights).quantize(Decimal("0.01")),
            total=total,
        ))

    # Read after the tenant is bound, like everything else here -- the row is
    # behind the same org_visible policy the room types are, so an unbranded
    # or another tenant's row is simply not there to find.
    brand = db.execute(
        text("SELECT brand_color, tagline, logo_key, banner_key "
             "FROM property.booking_branding WHERE property_id = :p"),
        {"p": prop["id"]},
    ).mappings().first()
    palette = theme(brand["brand_color"] if brand else None)

    return AvailabilityOut(
        property=PublicProperty(
            code=prop["code"], name=prop["name"],
            currency=prop["currency"] or "INR",
            checkin_time=prop["checkin_time"],
            checkout_time=prop["checkout_time"],
            city=prop["city"], country=prop["country"],
            branding=PublicBranding(
                brand_color=palette["brand_color"],
                ink_on_brand=palette["ink_on_brand"],
                tagline=brand["tagline"] if brand else None,
                # An image the store cannot serve is simply not offered, the
                # same rule the room photos follow: a broken logo looks worse
                # than the platform's default.
                logo_url=(photo_url(None, brand["logo_key"], None)
                          if brand else None),
                banner_url=(photo_url(None, brand["banner_key"], None)
                            if brand else None),
            ),
        ),
        arrival=arrival, departure=departure, nights=nights,
        adults=adults, children=children,
        room_types=out,
    )

# ---------------------------------------------------------------- booking --


class GuestIn(BaseModel):
    """The least a hotel needs to hold a room for somebody."""

    full_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=5, max_length=320)
    phone: str = Field(min_length=5, max_length=40)


class BookIn(BaseModel):
    arrival: date
    departure: date
    room_type_code: str
    adults: int = Field(2, ge=1, le=10)
    children: int = Field(0, ge=0, le=10)
    guest: GuestIn
    special_requests: str | None = Field(None, max_length=1000)


class BookOut(BaseModel):
    """A room held, and what it will cost to keep it."""

    reservation_id: uuid.UUID
    reservation_number: str
    room_type: str
    arrival: date
    departure: date
    nights: int
    total: Decimal
    currency: str
    #: The room is only held until this moment. Nothing is booked yet.
    expires_at: datetime
    #: The guest's one proof that this hold is theirs, needed to pay for it.
    #:
    #: Returned here and nowhere else, ever. It is not listed, not re-issued
    #: and not readable back -- only a hash of it is kept -- so a client that
    #: loses it has to hold the room again. That is the trade: a credential
    #: that can be fetched a second time is one an attacker can fetch too.
    payment_token: str


@public_router.post("/{property_code}/book", response_model=BookOut,
                    status_code=201)
def book(
    property_code: str,
    body: BookIn,
    db: Session = Depends(get_session),
):
    """Hold a room for a guest who is about to pay.

    **This books nothing.** It takes the room off sale for a few minutes so the
    guest can pay without it being sold underneath them, and returns what they
    owe. The booking becomes real when the gateway says the money moved — never
    on the strength of the guest reaching a page.

    That is also why the hold expires and why something reaps it: most people
    who start a checkout do not finish, and every one of them is holding a
    room. Without expiry the hotel sells out while standing empty.
    """
    # Only the property this code names is readable until its tenant is bound.
    property_code_context(db, code=property_code)
    prop = db.execute(
        text("SELECT p.id, p.organization_id, p.currency, p.timezone "
             "FROM iam.properties p "
             "WHERE p.code = :c AND p.status = 'active'" + _ENTITLED),
        {"c": property_code},
    ).mappings().first()
    if prop is None:
        raise HTTPException(status_code=404, detail="No such property.")
    # A guest has no login; the property named in the URL decides whose
    # records this request may read, and nothing wider.
    bind_tenant_context(db, organization_id=prop["organization_id"],
                        property_id=prop["id"])

    nights = (body.departure - body.arrival).days
    today = _local_today(prop["timezone"])
    if nights < 1:
        raise HTTPException(
            status_code=422,
            detail="Departure must be at least one night after arrival.")
    if nights > MAX_NIGHTS:
        raise HTTPException(
            status_code=422,
            detail=f"Stays longer than {MAX_NIGHTS} nights cannot be booked "
                   f"online — please contact the property.")
    if body.arrival < today:
        raise HTTPException(
            status_code=422, detail="Arrival cannot be in the past.")

    rt = db.execute(
        text("SELECT id, name, max_occupancy FROM property.room_types "
             "WHERE property_id = :p AND code = :c AND status = 'active'"),
        {"p": prop["id"], "c": body.room_type_code},
    ).mappings().first()
    if rt is None:
        raise HTTPException(status_code=404, detail="No such room type.")
    guests = body.adults + body.children
    if rt["max_occupancy"] is not None and guests > rt["max_occupancy"]:
        raise HTTPException(
            status_code=422,
            detail=f"{rt['name']} sleeps {rt['max_occupancy']}.")

    # Priced from the same rate calendar the availability search quoted, so a
    # guest is never held to a number they were not shown.
    total = Decimal(str(db.execute(
        text(
            """
            SELECT coalesce(sum(COALESCE(rc.rate, rt.base_rate, 0)), 0)
            FROM generate_series(CAST(:arr AS date),
                                 CAST(:dep AS date) - 1, interval '1 day') AS d
            CROSS JOIN property.room_types rt
            LEFT JOIN property.rate_calendar_days rc
                   ON rc.room_type_id = rt.id AND rc.stay_date = d::date
            WHERE rt.id = :rt
            """
        ),
        {"arr": body.arrival, "dep": body.departure, "rt": rt["id"]},
    ).scalar_one()))
    if total <= 0:
        raise HTTPException(
            status_code=409,
            detail="That room type is not priced for these dates.")

    guest_id = _guest_for(db, prop, body.guest)

    try:
        held = create_hold(
            db,
            organization_id=prop["organization_id"],
            property_id=prop["id"],
            room_type_id=rt["id"],
            arrival_date=body.arrival,
            departure_date=body.departure,
            units=1,
            adults=body.adults,
            children=body.children,
            guest_id=guest_id,
            # The schema already has a name for this channel. Inventing
            # "booking_engine" alongside it would split direct bookings
            # across two labels in every revenue report.
            source="website",
            special_requests=body.special_requests,
            # Unique per attempt: a guest who presses Book twice wants two
            # tries at the same room, not one hold silently returned twice.
            idempotency_key=f"ibe-{uuid.uuid4()}",
            hold_ttl_minutes=settings.hold_ttl_minutes,
            overbooking_allowance=0,
        )
    except InventoryShortage as exc:
        raise HTTPException(
            status_code=409,
            detail="That room has just been taken for these dates. Please "
                   "search again.") from exc

    db.execute(
        text("UPDATE booking.reservation_units SET nightly_rate = :r "
             "WHERE reservation_id = :res"),
        {"r": (total / nights).quantize(Decimal("0.01")),
         "res": held.reservation_id},
    )
    # The guest's capability to pay for this hold, minted here because this is
    # the only moment we are certainly talking to the person the room is being
    # held for. Stored as a hash: the only question ever asked of it is
    # "does the one presented match", and a hash answers that without the
    # database holding a working credential.
    payment_token = secrets.token_urlsafe(32)
    db.execute(
        text("UPDATE booking.booking_holds SET payment_token_hash = :h "
             "WHERE reservation_id = :r AND status = 'held'"),
        {"h": _token_hash(payment_token), "r": held.reservation_id},
    )

    expires = db.execute(
        text("SELECT expires_at FROM booking.booking_holds "
             "WHERE reservation_id = :r ORDER BY created_at DESC LIMIT 1"),
        {"r": held.reservation_id},
    ).scalar_one()

    return BookOut(
        reservation_id=held.reservation_id,
        reservation_number=held.number,
        room_type=rt["name"],
        arrival=body.arrival, departure=body.departure, nights=nights,
        total=total, currency=prop["currency"] or "INR",
        expires_at=expires,
        payment_token=payment_token,
    )


def _guest_for(db: Session, prop, g: GuestIn) -> uuid.UUID:
    """Find this guest, or record them.

    Matched on email within the organisation, which is the scope guests are
    actually kept in — a group's guest is the group's guest, not a separate
    stranger at each of its hotels. A returning guest should not become a new
    person every time they book, or nobody can see they are a regular.
    """
    email = g.email.strip().lower()
    found = db.execute(
        text("SELECT id FROM engagement.guests "
             "WHERE organization_id = :o AND lower(email) = :e LIMIT 1"),
        {"o": prop["organization_id"], "e": email},
    ).scalar()
    if found:
        return found
    gid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO engagement.guests
                (id, organization_id, full_name, email, phone)
            VALUES (:id, :org, :name, :email, :phone)
            """
        ),
        {"id": gid, "org": prop["organization_id"],
         "name": g.full_name.strip(), "email": email, "phone": g.phone.strip()},
    )
    return gid


def _token_hash(token: str) -> str:
    """What is stored for a hold's payment token.

    SHA-256 and nothing more. This is not a password: it is 256 bits of
    randomness that lives fifteen minutes, so there is no dictionary to stretch
    against and a slow KDF would buy nothing an attacker cares about.
    """
    return hashlib.sha256(token.encode()).hexdigest()


class PayIn(BaseModel):
    """A guest asking to pay for the room they are holding.

    Note what is *not* here: an amount. What a stay costs is the hotel's
    answer, not the payer's, and a public endpoint that accepted a figure from
    the internet would let anyone book a suite for one rupee. It is recomputed
    from the rooms actually held.
    """

    reservation_id: uuid.UUID
    #: Exactly as returned by ``/book``. Proves this hold is the caller's.
    payment_token: str = Field(min_length=16, max_length=200)


class PayOut(BaseModel):
    """What the guest's browser needs to open the gateway's checkout."""

    order_id: str
    amount: Decimal
    currency: str
    #: The gateway's publishable key. Empty means no real money will move --
    #: the property has no gateway configured and this is a rehearsal.
    key_id: str = ""
    #: Unchanged by paying. The room is released at this moment if the payment
    #: does not complete, which is why a checkout should show it.
    expires_at: datetime
    reservation_number: str


@public_router.post("/{property_code}/pay", response_model=PayOut)
def pay(
    property_code: str,
    body: PayIn,
    db: Session = Depends(get_session),
):
    """Open a payment for a hold, so the guest can actually pay for it.

    This is the step that was missing between holding a room and owning it.
    It creates nothing final: it opens an order at the property's own payment
    gateway and hands back what a checkout needs. **The booking becomes real in
    the webhook and nowhere else** -- a guest reaching a success page proves
    they closed a browser tab.

    Being public, it answers to the internet, and everything here follows from
    that:

    * **The property comes from the URL**, and must still have the booking
      engine switched on. A hotel that took itself off sale this morning does
      not keep taking money this afternoon.
    * **The hold must be live and the token must match.** An expired hold, a
      cancelled one, or a wrong token all get the same 404. Which of the three
      it was is not the caller's business -- told apart, they become an oracle
      for which reservation ids exist.
    * **The amount is ours.** Recomputed from the rooms on the booking at the
      rate stored when they were held, so it is the figure the guest was quoted
      and not one they supplied.
    * **Paying twice is not possible by accident.** Finance returns the
      existing order for a reservation that already has one, so a guest who
      reloads the checkout is sent back to the same payment rather than given a
      second one.
    """
    # Only the property this code names is readable until its tenant is bound.
    property_code_context(db, code=property_code)
    prop = db.execute(
        text("SELECT p.id, p.organization_id, p.currency "
             "FROM iam.properties p "
             "WHERE p.code = :c AND p.status = 'active'" + _ENTITLED),
        {"c": property_code},
    ).mappings().first()
    if prop is None:
        raise HTTPException(status_code=404, detail="No such property.")
    # A guest has no login; the property named in the URL decides whose
    # records this request may read, and nothing wider.
    bind_tenant_context(db, organization_id=prop["organization_id"],
                        property_id=prop["id"])

    hold = db.execute(
        text(
            """
            SELECT h.payment_token_hash, h.expires_at, r.number
            FROM booking.booking_holds h
            JOIN booking.reservations r ON r.id = h.reservation_id
            WHERE h.reservation_id = :r
              AND h.property_id = :p
              AND h.status = 'held'
              AND h.expires_at > now()
              AND r.status = 'held'
            ORDER BY h.created_at DESC
            LIMIT 1
            """
        ),
        {"r": body.reservation_id, "p": prop["id"]},
    ).mappings().first()

    # One answer for every way this can fail. A hold that expired, a booking
    # already paid for, a token that does not match and a reservation id that
    # was never ours are all "no": distinguishing them would tell a stranger
    # which reservation ids are real and how long they have to guess a token.
    if (hold is None or not hold["payment_token_hash"]
            or not hmac.compare_digest(hold["payment_token_hash"],
                                       _token_hash(body.payment_token))):
        raise HTTPException(
            status_code=404,
            detail="That hold is no longer available to pay for. It may have "
                   "expired -- please search again.")

    # What the stay costs, from the rooms actually held and the rate they were
    # held at. `departure - arrival` is the night count per room, so a booking
    # of three rooms over two nights adds up correctly rather than charging one
    # room's worth.
    amount = Decimal(str(db.execute(
        text(
            """
            SELECT coalesce(sum(u.nightly_rate
                                * (u.departure_date - u.arrival_date)), 0)
            FROM booking.reservation_units u
            WHERE u.reservation_id = :r AND u.status <> 'cancelled'
            """
        ),
        {"r": body.reservation_id},
    ).scalar_one()))
    if amount <= 0:
        # Nothing priced. Refusing beats opening a zero-value order that can
        # never be settled and leaves the guest staring at a broken checkout.
        log.error("reservation %s is held but has no priced rooms",
                  body.reservation_id)
        raise HTTPException(
            status_code=409,
            detail="This booking has no price yet -- please contact the "
                   "property.")

    intent = _open_intent(
        organization_id=prop["organization_id"],
        property_id=prop["id"],
        reservation_id=body.reservation_id,
        amount=amount,
        currency=prop["currency"] or "INR",
    )

    return PayOut(
        order_id=intent["order_id"],
        amount=Decimal(str(intent["amount"])),
        currency=intent["currency"],
        key_id=intent.get("key_id") or "",
        expires_at=hold["expires_at"],
        reservation_number=hold["number"],
    )


def _open_intent(*, organization_id, property_id, reservation_id,
                 amount: Decimal, currency: str) -> dict:
    """Ask finance to open the intent and create the gateway order.

    Over HTTP with the platform's own credential, rather than reaching into
    finance's tables from here. Opening an order needs the tenant's sealed
    gateway secrets, and a booking service that could read those would be a
    booking service that could spend them.

    No user is involved -- the guest has no account -- so this is the platform
    acting on its own behalf, which is exactly what a service credential is
    for. The organisation is named in the call and finance re-derives it from
    the reservation anyway, so a mistake here cannot reach another tenant's
    gateway.
    """
    if not settings.service_token:
        # Without the credential this call cannot be made at all. Said plainly
        # in the log, because the guest-facing symptom -- checkout simply does
        # not open -- says nothing about the cause.
        log.error("cannot open a payment intent: SERVICE_TOKEN is not set")
        raise HTTPException(
            status_code=503,
            detail="Online payment is not available right now.")
    try:
        resp = httpx.post(
            f"{settings.finance_url}/checkout/intents",
            headers={"X-Service-Token": settings.service_token,
                     "X-Service-Org": str(organization_id)},
            json={
                "property_id": str(property_id),
                "reservation_id": str(reservation_id),
                # Serialised as a string: a Decimal through JSON's float is how
                # a bill for 4000.00 becomes 3999.9999999999995.
                "amount": str(amount),
                "currency": currency,
            },
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        log.error("finance unreachable opening an intent for %s: %s",
                  reservation_id, exc)
        raise HTTPException(
            status_code=502,
            detail="Online payment is temporarily unavailable. Your room is "
                   "still held -- please try again in a moment.") from None

    if resp.status_code >= 400:
        # Finance's own refusals are the interesting ones (a reservation that
        # is no longer payable, say), but its wording is written for staff, so
        # the guest gets a plain sentence and the detail goes to the log.
        log.error("finance refused an intent for %s: %s %s",
                  reservation_id, resp.status_code, resp.text[:300])
        raise HTTPException(
            status_code=502,
            detail="This booking cannot be paid for online at the moment. "
                   "Please contact the property.")
    return resp.json()


class StatusOut(BaseModel):
    """Where a booking has got to, for a guest waiting on a page."""

    reservation_number: str
    #: 'held' while the payment is still in flight, 'confirmed' once the
    #: gateway's callback has been believed and the room is actually theirs.
    status: str
    #: True once the money has been credited to the folio. A booking can be
    #: paid but not yet confirmed for a few seconds, and telling a guest their
    #: card worked is the reassuring half of that.
    paid: bool
    arrival: date
    departure: date
    total: Decimal
    currency: str


@public_router.get("/{property_code}/booking/{reservation_id}",
                   response_model=StatusOut)
def booking_status(
    property_code: str,
    reservation_id: uuid.UUID,
    payment_token: str = Query(..., min_length=16, max_length=200),
    db: Session = Depends(get_session),
):
    """Where this booking has got to. For polling after a guest has paid.

    The browser does not decide whether a payment worked -- the signed webhook
    does, and it arrives on its own schedule, a second or two after the guest
    closes the gateway's window. Without something to ask, a checkout has two
    bad options: claim success it cannot verify, or leave the guest staring at
    a spinner. This is the third.

    Authorised by the same token that paid for it, and deliberately not
    restricted to live holds: the entire point is to watch a hold *stop* being
    one. A wrong token is a 404, exactly as everywhere else here.
    """
    # Only the property this code names is readable until its tenant is bound.
    property_code_context(db, code=property_code)
    prop = db.execute(
        text("SELECT p.id, p.organization_id, p.currency FROM iam.properties p "
             "WHERE p.code = :c AND p.status = 'active'" + _ENTITLED),
        {"c": property_code},
    ).mappings().first()
    if prop is None:
        raise HTTPException(status_code=404, detail="No such property.")
    # A guest has no login; the property named in the URL decides whose
    # records this request may read, and nothing wider.
    bind_tenant_context(db, organization_id=prop["organization_id"],
                        property_id=prop["id"])

    row = db.execute(
        text(
            """
            SELECT h.payment_token_hash, r.number, r.status,
                   min(u.arrival_date) AS arrival,
                   max(u.departure_date) AS departure,
                   coalesce(sum(u.nightly_rate
                                * (u.departure_date - u.arrival_date)), 0)
                       AS total
            FROM booking.booking_holds h
            JOIN booking.reservations r ON r.id = h.reservation_id
            LEFT JOIN booking.reservation_units u
                   ON u.reservation_id = r.id AND u.status <> 'cancelled'
            WHERE h.reservation_id = :r AND h.property_id = :p
            GROUP BY h.payment_token_hash, r.number, r.status, h.created_at
            ORDER BY h.created_at DESC
            LIMIT 1
            """
        ),
        {"r": reservation_id, "p": prop["id"]},
    ).mappings().first()

    if (row is None or not row["payment_token_hash"]
            or not hmac.compare_digest(row["payment_token_hash"],
                                       _token_hash(payment_token))):
        raise HTTPException(status_code=404, detail="No such booking.")

    # Asked of finance's own record rather than inferred from the reservation.
    # "Confirmed" and "paid" are different facts with different timings, and a
    # guest whose card was charged deserves to be told so even in the seconds
    # before the booking flips.
    paid = bool(db.execute(
        text("SELECT 1 FROM finance.payment_intents "
             "WHERE reservation_id = :r AND status = 'succeeded' LIMIT 1"),
        {"r": reservation_id},
    ).first())

    return StatusOut(
        reservation_number=row["number"],
        status=row["status"],
        paid=paid,
        arrival=row["arrival"],
        departure=row["departure"],
        total=Decimal(str(row["total"])),
        currency=prop["currency"] or "INR",
    )
