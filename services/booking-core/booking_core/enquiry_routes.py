"""Waitlist and Enquiries (screen 032).

A board of demand that has not become a booking. Five columns — New, Contacted,
Waitlisted, Offered, Converted — plus ``lost``, which is how something leaves
the board rather than a column on it.

**The availability match is real.** The mockup's "Availability Match Found"
panel is not a suggestion engine; it is the same inventory arithmetic the
booking path uses, asked on the enquiry's behalf. If a guest wanted a Deluxe
Sea View from the 18th and one is now sellable for every night of that stay,
that is a fact, and it is worth putting in front of somebody. Availability is
taken as the *minimum* across the nights for the same reason it is on the
booking screen: a stay needs the same room every night.

**Converting creates a real booking**, through ``create_hold`` and
``confirm_reservation`` — the same row-locked path the New Reservation screen
uses, so an enquiry cannot conjure a room that is not there. The enquiry keeps
its channel, so a booking that began as a phone enquiry does not become an
anonymous "direct" one.

Two things on the mockup that are not here, and why:

* **Send Offer.** There is no mail or messaging transport anywhere in this
  system. A button that marked an enquiry "Offered" while sending nothing
  would be worse than no button — it would put a lie in the audit trail. The
  status can still be moved to Offered by hand, which is honest: it records
  that somebody made an offer, not that this system delivered one.
* **The AI conversion probability.** There is no model and no history to build
  one from. A number like "78% likely" invented in the frontend would be acted
  on by a real person deciding where to spend their afternoon.

The week-on-week trends *are* computed, from ``created_at``. With a young
table they will read zero, which is true.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import (
    Caller, assert_property_in_org, build_authz, require_property_permission,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .flow import confirm_reservation
from .rooms_routes import photo_url
from .inventory import InventoryShortage, create_hold
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

enquiry_router = APIRouter(tags=["enquiries"], route_class=TransactionalRoute)

# The five board columns, in the order they are worked.
BOARD = ("new", "contacted", "waitlisted", "offered", "converted")
OPEN_STATUSES = ("new", "contacted", "waitlisted", "offered")

STATUS_LABELS = {
    "new": "New", "contacted": "Contacted", "waitlisted": "Waitlisted",
    "offered": "Offered", "converted": "Converted", "lost": "Lost",
}
CHANNEL_LABELS = {
    "direct": "Direct", "website": "Website", "phone": "Phone",
    "email": "Email", "walk_in": "Walk-in", "ota": "OTA",
    "travel_agent": "Travel Agent", "corporate": "Corporate",
    "group": "Group", "other": "Other",
}
CHANNELS = tuple(CHANNEL_LABELS)


class EnquiryCard(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    arrival_date: date | None
    departure_date: date | None
    nights: int | None
    adults: int
    children: int
    room_type_id: uuid.UUID | None
    room_type: str | None
    budget_min: Decimal | None
    budget_max: Decimal | None
    channel: str | None
    channel_label: str | None
    status: str
    next_follow_up_at: datetime | None
    # True when the follow-up is today or already past.
    follow_up_due: bool
    notes: str | None
    converted_reservation_id: uuid.UUID | None
    reservation_number: str | None
    created_at: datetime


class Trend(BaseModel):
    value: int
    # Change against the previous seven days. None when there is no history to
    # compare against rather than a fabricated zero.
    delta: int | None


class BoardKpis(BaseModel):
    new_enquiries: Trend
    waitlisted: Trend
    follow_ups_due: int
    converted: Trend


class Column(BaseModel):
    key: str
    label: str
    count: int
    cards: list[EnquiryCard]


class BoardOut(BaseModel):
    columns: list[Column]
    kpis: BoardKpis
    channels: list[dict]
    room_types: list[dict]
    lost_count: int
    can_create: bool
    can_convert: bool


class MatchRoomType(BaseModel):
    room_type_id: uuid.UUID
    name: str
    sellable: int
    nights: int
    nights_loaded: int
    blocked_reason: str | None
    # Per night, and for the whole stay. A guest's budget is almost always
    # the second one, so that is what within_budget compares.
    rate: Decimal | None
    stay_total: Decimal | None
    within_budget: bool | None
    is_requested: bool
    photo_url: str | None


class MatchOut(BaseModel):
    enquiry_id: uuid.UUID
    arrival_date: date | None
    departure_date: date | None
    nights: int | None
    checked: bool
    reason: str | None
    requested: MatchRoomType | None
    alternatives: list[MatchRoomType]
    note: str


class EnquiryIn(BaseModel):
    property_id: uuid.UUID
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    arrival_date: date | None = None
    departure_date: date | None = None
    adults: int = Field(default=2, ge=1)
    children: int = Field(default=0, ge=0)
    room_type_id: uuid.UUID | None = None
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    channel: str | None = None
    next_follow_up_at: datetime | None = None
    notes: str | None = None


class MoveIn(BaseModel):
    property_id: uuid.UUID
    status: str
    reason: str | None = None
    next_follow_up_at: datetime | None = None


class ConvertIn(BaseModel):
    property_id: uuid.UUID
    room_type_id: uuid.UUID | None = None
    arrival_date: date | None = None
    departure_date: date | None = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_CARD_SQL = """
    SELECT e.id, e.full_name, e.email, e.phone, e.arrival_date,
           e.departure_date, e.adults, e.children, e.room_type_id,
           e.budget_min, e.budget_max, e.channel, e.status,
           e.next_follow_up_at, e.notes, e.converted_reservation_id,
           e.created_at,
           rt.name AS room_type,
           r.number AS reservation_number
    FROM engagement.enquiries e
    LEFT JOIN property.room_types rt ON rt.id = e.room_type_id
    LEFT JOIN booking.reservations r ON r.id = e.converted_reservation_id
    WHERE e.property_id = :prop
"""


def _card(row, now: datetime) -> EnquiryCard:
    nights = None
    if row["arrival_date"] and row["departure_date"]:
        nights = (row["departure_date"] - row["arrival_date"]).days
    due = bool(row["next_follow_up_at"] and row["next_follow_up_at"] <= now
               and row["status"] in OPEN_STATUSES)
    return EnquiryCard(
        id=row["id"], full_name=row["full_name"], email=row["email"],
        phone=row["phone"], arrival_date=row["arrival_date"],
        departure_date=row["departure_date"], nights=nights,
        adults=row["adults"], children=row["children"],
        room_type_id=row["room_type_id"], room_type=row["room_type"],
        budget_min=row["budget_min"], budget_max=row["budget_max"],
        channel=row["channel"],
        channel_label=CHANNEL_LABELS.get(row["channel"] or "") or None,
        status=row["status"], next_follow_up_at=row["next_follow_up_at"],
        follow_up_due=due, notes=row["notes"],
        converted_reservation_id=row["converted_reservation_id"],
        reservation_number=row["reservation_number"],
        created_at=row["created_at"],
    )


def _may(db: Session, caller: Caller, property_id: uuid.UUID,
         action: str) -> bool:
    from chirala_common.authz import _GRANT_SQL
    return db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "reservations", "act": action,
         "prop": str(property_id)},
    ).first() is not None


def _load(db: Session, enquiry_id: uuid.UUID, property_id: uuid.UUID) -> dict:
    row = db.execute(
        text("SELECT * FROM engagement.enquiries "
             "WHERE id = :i AND property_id = :p"),
        {"i": enquiry_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    return dict(row)


def _trend(db: Session, property_id: uuid.UUID, where: str) -> Trend:
    """This week against the seven days before it, from created_at."""
    row = db.execute(
        text(
            f"""
            SELECT
              count(*) FILTER (WHERE {where}) AS total,
              count(*) FILTER (WHERE {where}
                AND e.created_at >= now() - INTERVAL '7 days') AS this_week,
              count(*) FILTER (WHERE {where}
                AND e.created_at >= now() - INTERVAL '14 days'
                AND e.created_at <  now() - INTERVAL '7 days') AS last_week,
              min(e.created_at) AS oldest
            FROM engagement.enquiries e
            WHERE e.property_id = :p
            """
        ),
        {"p": property_id},
    ).mappings().first()
    # No comparison is possible until there are two weeks of history. Saying
    # nothing beats printing a +0% that looks like flat demand.
    delta = None
    if row["oldest"] is not None:
        age = datetime.now(row["oldest"].tzinfo) - row["oldest"]
        if age.days >= 14:
            delta = int(row["this_week"]) - int(row["last_week"])
    return Trend(value=int(row["total"]), delta=delta)


# --------------------------------------------------------------------------
# The board
# --------------------------------------------------------------------------
@enquiry_router.get("/enquiries", response_model=BoardOut)
def board(
    property_id: uuid.UUID,
    q: str | None = Query(None),
    channel: str | None = Query(None),
    room_type_id: uuid.UUID | None = Query(None),
    due: bool = Query(False, description="Only enquiries whose follow-up is due"),
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """Five columns of open demand, plus the counts across the top."""
    assert_property_in_org(db, caller, property_id)
    now = db.execute(text("SELECT now()")).scalar_one()

    sql = _CARD_SQL + """
          AND e.status <> 'lost'
          AND (CAST(:q AS text) IS NULL
               OR e.full_name ILIKE '%' || CAST(:q AS text) || '%'
               OR COALESCE(e.email, '') ILIKE '%' || CAST(:q AS text) || '%'
               OR COALESCE(e.phone, '') ILIKE '%' || CAST(:q AS text) || '%')
          AND (CAST(:ch AS text) IS NULL OR e.channel = CAST(:ch AS text))
          AND (CAST(:rt AS uuid) IS NULL
               OR e.room_type_id = CAST(:rt AS uuid))
        ORDER BY e.next_follow_up_at NULLS LAST, e.created_at DESC
    """
    rows = db.execute(
        text(sql),
        {"prop": property_id, "q": q or None, "ch": channel or None,
         "rt": str(room_type_id) if room_type_id else None},
    ).mappings().all()

    cards = [_card(r, now) for r in rows]
    if due:
        cards = [c for c in cards if c.follow_up_due]

    columns = [
        Column(key=k, label=STATUS_LABELS[k],
               count=sum(1 for c in cards if c.status == k),
               cards=[c for c in cards if c.status == k])
        for k in BOARD
    ]

    follow_ups = db.execute(
        text("SELECT count(*) FROM engagement.enquiries "
             "WHERE property_id = :p AND status = ANY(:open) "
             "AND next_follow_up_at IS NOT NULL AND next_follow_up_at <= now()"),
        {"p": property_id, "open": list(OPEN_STATUSES)},
    ).scalar_one()

    channels = [
        {"value": r[0], "label": CHANNEL_LABELS.get(r[0], r[0]), "count": r[1]}
        for r in db.execute(
            text("SELECT channel, count(*) FROM engagement.enquiries "
                 "WHERE property_id = :p AND channel IS NOT NULL "
                 "AND status <> 'lost' GROUP BY 1 ORDER BY 1"),
            {"p": property_id},
        )
    ]
    room_types = [
        {"value": str(r[0]), "label": r[1]}
        for r in db.execute(
            text("SELECT id, name FROM property.room_types "
                 "WHERE property_id = :p ORDER BY name"),
            {"p": property_id},
        )
    ]
    lost = db.execute(
        text("SELECT count(*) FROM engagement.enquiries "
             "WHERE property_id = :p AND status = 'lost'"),
        {"p": property_id},
    ).scalar_one()

    return BoardOut(
        columns=columns,
        kpis=BoardKpis(
            new_enquiries=_trend(db, property_id, "e.status = 'new'"),
            waitlisted=_trend(db, property_id, "e.status = 'waitlisted'"),
            follow_ups_due=int(follow_ups),
            converted=_trend(db, property_id, "e.status = 'converted'"),
        ),
        channels=channels, room_types=room_types, lost_count=int(lost),
        can_create=_may(db, caller, property_id, "create"),
        can_convert=_may(db, caller, property_id, "edit"),
    )


# --------------------------------------------------------------------------
# Create and edit
# --------------------------------------------------------------------------
@enquiry_router.post("/enquiries", response_model=EnquiryCard, status_code=201)
def create_enquiry(
    body: EnquiryIn,
    caller: Caller = Depends(require_permission("reservations", "create")),
    db: Session = Depends(get_session),
):
    """Record someone who asked."""
    require_property_permission(db, caller, body.property_id,
                                "reservations", "create")
    if body.channel and body.channel not in CHANNELS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown channel. Use one of: {', '.join(CHANNELS)}.")
    if (body.arrival_date and body.departure_date
            and body.departure_date <= body.arrival_date):
        raise HTTPException(
            status_code=422, detail="Departure must be after arrival.")

    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": body.property_id},
    ).scalar_one()

    # An enquirer who is already a guest is linked; nobody new is created.
    guest_id = None
    if body.email or body.phone:
        guest_id = db.execute(
            text("SELECT id FROM engagement.guests WHERE organization_id = :o "
                 "AND ((CAST(:em AS text) IS NOT NULL AND email = CAST(:em AS text)) "
                 "  OR (CAST(:ph AS text) IS NOT NULL AND phone = CAST(:ph AS text))) "
                 "LIMIT 1"),
            {"o": org, "em": body.email or None, "ph": body.phone or None},
        ).scalar()

    eid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO engagement.enquiries
                (id, organization_id, property_id, guest_id, full_name, email,
                 phone, arrival_date, departure_date, adults, children,
                 room_type_id, budget_min, budget_max, channel, status,
                 next_follow_up_at, notes, created_by, updated_by)
            VALUES (:id, :org, :prop, :guest, :name, :email, :phone, :arr,
                    :dep, :ad, :ch, :rt, :bmin, :bmax, :chan, 'new',
                    :follow, :notes, :who, :who)
            """
        ),
        {"id": eid, "org": org, "prop": body.property_id, "guest": guest_id,
         "name": body.full_name.strip(), "email": body.email or None,
         "phone": body.phone or None, "arr": body.arrival_date,
         "dep": body.departure_date, "ad": body.adults, "ch": body.children,
         "rt": body.room_type_id, "bmin": body.budget_min,
         "bmax": body.budget_max, "chan": body.channel,
         "follow": body.next_follow_up_at, "notes": body.notes,
         "who": caller.user_id},
    )
    record_audit(
        db, action="enquiry.created", entity_type="enquiry", entity_id=str(eid),
        organization_id=org, property_id=body.property_id,
        actor_subject=caller.subject,
        after={"name": body.full_name, "channel": body.channel,
               "arrival": str(body.arrival_date) if body.arrival_date else None},
    )
    now = db.execute(text("SELECT now()")).scalar_one()
    row = db.execute(text(_CARD_SQL + " AND e.id = :id"),
                     {"prop": body.property_id, "id": eid}).mappings().first()
    return _card(row, now)


@enquiry_router.patch("/enquiries/{enquiry_id}", response_model=EnquiryCard)
def update_enquiry(
    enquiry_id: uuid.UUID,
    body: EnquiryIn,
    caller: Caller = Depends(require_permission("reservations", "edit")),
    db: Session = Depends(get_session),
):
    """Correct the details of an enquiry. Status is moved separately."""
    require_property_permission(db, caller, body.property_id,
                                "reservations", "edit")
    before = _load(db, enquiry_id, body.property_id)
    if before["status"] == "converted":
        raise HTTPException(
            status_code=409,
            detail="This enquiry became a booking. Change the booking instead.")

    db.execute(
        text(
            """
            UPDATE engagement.enquiries SET
                full_name = :name, email = :email, phone = :phone,
                arrival_date = :arr, departure_date = :dep,
                adults = :ad, children = :ch, room_type_id = :rt,
                budget_min = :bmin, budget_max = :bmax, channel = :chan,
                next_follow_up_at = :follow, notes = :notes,
                updated_by = :who, updated_at = now(), version = version + 1
            WHERE id = :id
            """
        ),
        {"name": body.full_name.strip(), "email": body.email or None,
         "phone": body.phone or None, "arr": body.arrival_date,
         "dep": body.departure_date, "ad": body.adults, "ch": body.children,
         "rt": body.room_type_id, "bmin": body.budget_min,
         "bmax": body.budget_max, "chan": body.channel,
         "follow": body.next_follow_up_at, "notes": body.notes,
         "who": caller.user_id, "id": enquiry_id},
    )
    record_audit(
        db, action="enquiry.updated", entity_type="enquiry",
        entity_id=str(enquiry_id), organization_id=before["organization_id"],
        property_id=body.property_id, actor_subject=caller.subject,
        before={"name": before["full_name"]}, after={"name": body.full_name},
    )
    now = db.execute(text("SELECT now()")).scalar_one()
    row = db.execute(text(_CARD_SQL + " AND e.id = :id"),
                     {"prop": body.property_id, "id": enquiry_id}).mappings().first()
    return _card(row, now)


@enquiry_router.post("/enquiries/{enquiry_id}/move", response_model=EnquiryCard)
def move_enquiry(
    enquiry_id: uuid.UUID,
    body: MoveIn,
    caller: Caller = Depends(require_permission("reservations", "edit")),
    db: Session = Depends(get_session),
):
    """Move an enquiry between columns, or close it as lost.

    ``converted`` is not reachable here: it is set by the conversion endpoint,
    which has an actual booking to point at. The check constraint would refuse
    it anyway, and that is the right place for the rule to live.
    """
    require_property_permission(db, caller, body.property_id,
                                "reservations", "edit")
    before = _load(db, enquiry_id, body.property_id)

    if body.status == "converted":
        raise HTTPException(
            status_code=422,
            detail="Convert an enquiry through Convert to Booking, so there is "
                   "a real reservation behind the status.")
    if body.status not in (*OPEN_STATUSES, "lost"):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status. Use one of: "
                   f"{', '.join((*OPEN_STATUSES, 'lost'))}.")
    if before["status"] == "converted":
        raise HTTPException(
            status_code=409,
            detail="This enquiry became a booking and cannot be moved back.")
    if body.status == "lost" and not (body.reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Say why it was lost — an unexplained loss teaches nobody "
                   "anything.")

    db.execute(
        # CAST explicitly: a bare :s used both as a value and inside a CASE
        # gives Postgres nothing to infer a type from.
        text("UPDATE engagement.enquiries SET status = CAST(:s AS varchar), "
             "lost_reason = CASE WHEN CAST(:s AS varchar) = 'lost' "
             "                   THEN :r ELSE lost_reason END, "
             "next_follow_up_at = COALESCE(:follow, next_follow_up_at), "
             "updated_by = :who, updated_at = now(), version = version + 1 "
             "WHERE id = :id"),
        {"s": body.status, "r": body.reason, "follow": body.next_follow_up_at,
         "who": caller.user_id, "id": enquiry_id},
    )
    record_audit(
        db, action=f"enquiry.{body.status}", entity_type="enquiry",
        entity_id=str(enquiry_id), organization_id=before["organization_id"],
        property_id=body.property_id, actor_subject=caller.subject,
        reason=body.reason, before={"status": before["status"]},
        after={"status": body.status},
    )
    now = db.execute(text("SELECT now()")).scalar_one()
    row = db.execute(text(_CARD_SQL + " AND e.id = :id"),
                     {"prop": body.property_id, "id": enquiry_id}).mappings().first()
    return _card(row, now)


# --------------------------------------------------------------------------
# Availability match
# --------------------------------------------------------------------------
# The same arithmetic the booking screen uses, asked on the enquiry's behalf.
# Sellable is the MINIMUM across the nights: a stay needs the same room every
# night, so three free on Monday and none on Tuesday sells nothing.
_MATCH_SQL = """
    SELECT rt.id, rt.name,
           COALESCE(MIN(d.physical_capacity - d.out_of_service - d.held_units
                        - d.reserved_units - d.allotment_units), 0) AS sellable,
           count(d.stay_date) AS nights_loaded,
           (SELECT p.url FROM property.room_type_photos p
             WHERE p.room_type_id = rt.id
             ORDER BY p.is_primary DESC, p.sort_order LIMIT 1) AS photo_url,
           (SELECT p.storage_key FROM property.room_type_photos p
             WHERE p.room_type_id = rt.id
             ORDER BY p.is_primary DESC, p.sort_order LIMIT 1) AS photo_key,
           -- The published rate where the calendar has one, the room type's
           -- base rate otherwise. Same precedence /reservations/{id}/detail
           -- uses, so a quoted price does not change between screens.
           COALESCE(
               (SELECT MIN(c.rate) FROM property.rate_calendar_days c
                 WHERE c.room_type_id = rt.id
                   AND c.property_id = rt.property_id
                   AND c.rate IS NOT NULL
                   AND c.stay_date >= :arr AND c.stay_date < :dep),
               rt.base_rate
           ) AS rate
    FROM property.room_types rt
    LEFT JOIN booking.room_type_inventory_days d
           ON d.room_type_id = rt.id
          AND d.property_id = rt.property_id
          AND d.stay_date >= :arr AND d.stay_date < :dep
    WHERE rt.property_id = :prop
    GROUP BY rt.id, rt.name, rt.base_rate
    ORDER BY rt.name
"""


@enquiry_router.get("/enquiries/{enquiry_id}/match", response_model=MatchOut)
def match_enquiry(
    enquiry_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """Is there anything that fits what this guest asked for?"""
    assert_property_in_org(db, caller, property_id)
    e = _load(db, enquiry_id, property_id)

    if not (e["arrival_date"] and e["departure_date"]):
        return MatchOut(
            enquiry_id=enquiry_id, arrival_date=e["arrival_date"],
            departure_date=e["departure_date"], nights=None, checked=False,
            reason="no_dates", requested=None, alternatives=[],
            note="This enquiry has no dates, so there is nothing to check "
                 "availability against. Add the dates the guest asked for.",
        )

    nights = (e["departure_date"] - e["arrival_date"]).days
    rows = db.execute(
        text(_MATCH_SQL),
        {"prop": property_id, "arr": e["arrival_date"], "dep": e["departure_date"]},
    ).mappings().all()

    bmin, bmax = e["budget_min"], e["budget_max"]

    def build(r) -> MatchRoomType:
        loaded = int(r["nights_loaded"])
        sellable = max(0, int(r["sellable"]))
        if loaded < nights:
            # A night with no inventory row is an absence of information, not
            # availability. Promising a room nobody has loaded is worse than
            # saying "not on sale".
            reason, sellable = "not_loaded", 0
        elif sellable <= 0:
            reason = "sold_out"
        else:
            reason = None
        rate = r["rate"]
        # What the guest would actually pay, which is what their budget was
        # about — comparing a nightly rate to a stay budget would call a
        # 19,500 stay "over" a 30,000 budget.
        stay_total = rate * nights if rate is not None else None
        within = None
        if stay_total is not None and (bmin is not None or bmax is not None):
            within = ((bmin is None or stay_total >= bmin)
                      and (bmax is None or stay_total <= bmax))
        return MatchRoomType(
            room_type_id=r["id"], name=r["name"], sellable=sellable,
            nights=nights, nights_loaded=loaded, blocked_reason=reason,
            rate=rate, stay_total=stay_total, within_budget=within,
            is_requested=r["id"] == e["room_type_id"],
            photo_url=photo_url(r["photo_url"], r["photo_key"]),
        )

    built = [build(r) for r in rows]
    requested = next((b for b in built if b.is_requested), None)
    alternatives = [b for b in built
                    if not b.is_requested and b.blocked_reason is None]
    # Cheapest first among what is actually free — a guest with a budget cares
    # about that order more than alphabetical.
    alternatives.sort(key=lambda b: (b.stay_total is None, b.stay_total or 0))

    if requested and requested.blocked_reason is None:
        note = (f"{requested.name} is available for all {nights} night"
                f"{'' if nights == 1 else 's'} — {requested.sellable} left.")
        if requested.within_budget is False:
            note += (f" At {requested.stay_total:,.0f} for the stay it is "
                     f"outside the budget the guest gave.")
    elif requested:
        note = (f"{requested.name} is "
                f"{'sold out' if requested.blocked_reason == 'sold_out' else 'not on sale'}"
                f" for these dates.")
        note += (f" {len(alternatives)} other room type"
                 f"{'' if len(alternatives) == 1 else 's'} could work."
                 if alternatives else " Nothing else is free either.")
    elif alternatives:
        note = (f"No room type was requested. {len(alternatives)} "
                f"are available for these dates.")
    else:
        note = "Nothing is available for these dates."

    return MatchOut(
        enquiry_id=enquiry_id, arrival_date=e["arrival_date"],
        departure_date=e["departure_date"], nights=nights, checked=True,
        reason=None, requested=requested, alternatives=alternatives, note=note,
    )


# --------------------------------------------------------------------------
# Convert
# --------------------------------------------------------------------------
@enquiry_router.post("/enquiries/{enquiry_id}/convert", response_model=EnquiryCard)
def convert_enquiry(
    enquiry_id: uuid.UUID,
    body: ConvertIn,
    caller: Caller = Depends(require_permission("reservations", "create")),
    db: Session = Depends(get_session),
):
    """Turn an enquiry into a real booking.

    Through ``create_hold`` and ``confirm_reservation`` — the same row-locked,
    constraint-guarded path the New Reservation screen uses, so an enquiry
    cannot conjure a room that is not there. If the room has gone, the guest
    stays on the board with the shortage reported, rather than the enquiry
    being marked converted against a booking that does not exist.
    """
    require_property_permission(db, caller, body.property_id,
                                "reservations", "create")
    e = _load(db, enquiry_id, body.property_id)
    if e["status"] == "converted":
        raise HTTPException(
            status_code=409, detail="This enquiry has already been converted.")
    if e["status"] == "lost":
        raise HTTPException(
            status_code=409,
            detail="This enquiry was closed as lost. Reopen it first.")

    arrival = body.arrival_date or e["arrival_date"]
    departure = body.departure_date or e["departure_date"]
    room_type_id = body.room_type_id or e["room_type_id"]
    missing = [n for n, v in (("dates", arrival and departure),
                              ("a room type", room_type_id)) if not v]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"This enquiry needs {' and '.join(missing)} before it can "
                   f"become a booking.")

    # The enquirer becomes a guest here and not before: creating one for every
    # enquiry would fill the directory with people who never came.
    guest_id = e["guest_id"]
    if guest_id is None:
        guest_id = uuid.uuid4()
        db.execute(
            text("INSERT INTO engagement.guests "
                 "(id, organization_id, full_name, email, phone) "
                 "VALUES (:id, :org, :name, :em, :ph)"),
            {"id": guest_id, "org": e["organization_id"],
             "name": e["full_name"], "em": e["email"], "ph": e["phone"]},
        )

    try:
        result = create_hold(
            db, organization_id=e["organization_id"],
            property_id=body.property_id, room_type_id=room_type_id,
            arrival_date=arrival, departure_date=departure, units=1,
            adults=e["adults"], children=e["children"],
            idempotency_key=f"enquiry:{enquiry_id}",
            hold_ttl_minutes=settings.hold_ttl_minutes,
            overbooking_allowance=settings.overbooking_allowance,
            guest_id=guest_id,
            # The booking keeps where it came from rather than becoming an
            # anonymous "direct" one.
            source=e["channel"],
            special_requests=e["notes"],
        )
    except InventoryShortage as exc:
        raise HTTPException(
            status_code=409,
            detail=f"That room is no longer free for these dates, so the "
                   f"enquiry has been left on the board. {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    confirm_reservation(db, reservation_id=result.reservation_id)

    db.execute(
        text("UPDATE engagement.enquiries SET status = 'converted', "
             "converted_reservation_id = :r, converted_at = now(), "
             "guest_id = :g, updated_by = :who, updated_at = now(), "
             "version = version + 1 WHERE id = :id"),
        {"r": result.reservation_id, "g": guest_id, "who": caller.user_id,
         "id": enquiry_id},
    )
    record_audit(
        db, action="enquiry.converted", entity_type="enquiry",
        entity_id=str(enquiry_id), organization_id=e["organization_id"],
        property_id=body.property_id, actor_subject=caller.subject,
        before={"status": e["status"]},
        after={"status": "converted",
               "reservation_id": str(result.reservation_id),
               "arrival": str(arrival), "departure": str(departure)},
    )
    now = db.execute(text("SELECT now()")).scalar_one()
    row = db.execute(text(_CARD_SQL + " AND e.id = :id"),
                     {"prop": body.property_id, "id": enquiry_id}).mappings().first()
    return _card(row, now)
