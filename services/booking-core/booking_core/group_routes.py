"""Group blocks: agree them, see what has been taken, give back the rest.

The reasoning behind the model is in ``group_blocks.py``. This is the HTTP
surface over it, and it is deliberately thin -- the counting and the inventory
moves belong in one place, because they are the part that must be right.

One decision worth naming here: creating and releasing a block need the
permission that changes a booking, not the one that reads it. A block takes
rooms off sale for a month, which costs the hotel more than most single
bookings do, and releasing one puts thirty rooms back on the market. Neither
is a view.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import group_blocks
from .flow import confirm_reservation
from .inventory import HoldLine, create_hold
from .database import get_session
from .inventory import InventoryOversold, InventoryShortage
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

group_router = APIRouter(tags=["group blocks"], route_class=TransactionalRoute)


class BlockLineIn(BaseModel):
    room_type_id: uuid.UUID
    rooms_blocked: int = Field(ge=1, le=500)
    nightly_rate: Decimal | None = Field(default=None, ge=0)


class BlockIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    arrival_date: date
    departure_date: date
    lines: list[BlockLineIn] = Field(min_length=1)
    cut_off_date: date | None = None
    commitment: str = "tentative"
    commercial_account_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=500)


class BlockLineOut(BaseModel):
    room_type_id: uuid.UUID
    room_type: str
    rooms_blocked: int
    rooms_picked_up: int
    rooms_still_held: int
    nightly_rate: Decimal | None


class BlockOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    commitment: str
    arrival_date: date
    departure_date: date
    cut_off_date: date | None
    commercial_account_id: uuid.UUID | None
    account_name: str | None
    notes: str | None
    lines: list[BlockLineOut]
    rooms_blocked: int
    rooms_picked_up: int
    rooms_still_held: int


class BlockRow(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    commitment: str
    arrival_date: date
    departure_date: date
    cut_off_date: date | None
    account_name: str | None
    rooms_blocked: int
    rooms_picked_up: int


def _out(db: Session, block_id: uuid.UUID) -> BlockOut:
    s = group_blocks.summary(db, block_id)
    b, lines = s["block"], s["lines"]
    return BlockOut(
        **{k: b[k] for k in (
            "id", "code", "name", "status", "commitment", "arrival_date",
            "departure_date", "cut_off_date", "commercial_account_id",
            "notes")},
        account_name=b["account_name"],
        lines=[BlockLineOut(**ln) for ln in lines],
        rooms_blocked=sum(ln["rooms_blocked"] for ln in lines),
        rooms_picked_up=sum(ln["rooms_picked_up"] for ln in lines),
        rooms_still_held=sum(ln["rooms_still_held"] for ln in lines),
    )


@group_router.post("/properties/{property_id}/group-blocks",
                   response_model=BlockOut,
                   status_code=status.HTTP_201_CREATED)
def create_block(
    property_id: uuid.UUID,
    body: BlockIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Agree a block and take its rooms off sale."""
    assert_property_in_org(db, caller, property_id)
    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar_one()
    try:
        block_id = group_blocks.create_block(
            db, organization_id=org, property_id=property_id,
            name=body.name, arrival_date=body.arrival_date,
            departure_date=body.departure_date,
            lines=[group_blocks.BlockLine(
                room_type_id=ln.room_type_id, rooms_blocked=ln.rooms_blocked,
                nightly_rate=ln.nightly_rate) for ln in body.lines],
            cut_off_date=body.cut_off_date, commitment=body.commitment,
            commercial_account_id=body.commercial_account_id,
            notes=body.notes, created_by=caller.user_id,
        )
    except group_blocks.BlockError as exc:
        raise HTTPException(422, str(exc)) from exc
    except InventoryOversold as exc:
        # The same refusal a booking gets, and worth its own message: a block
        # is the one place somebody is likely to ask for more rooms than exist
        # and be surprised that it is not allowed.
        raise HTTPException(
            409, f"There are not enough rooms to block: {exc}") from exc

    record_audit(
        db, actor_subject=caller.subject, action="group_block.created",
        entity_type="group_block", entity_id=str(block_id),
        property_id=property_id,
        after={"name": body.name, "arrival": str(body.arrival_date),
               "departure": str(body.departure_date),
               "rooms": sum(ln.rooms_blocked for ln in body.lines)},
    )
    return _out(db, block_id)


@group_router.get("/properties/{property_id}/group-blocks",
                  response_model=list[BlockRow])
def list_blocks(
    property_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Every block for this property, soonest arrival first."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT b.id, b.code, b.name, b.status, b.commitment,
                   b.arrival_date, b.departure_date, b.cut_off_date,
                   ca.name AS account_name,
                   COALESCE((SELECT sum(l.rooms_blocked)
                               FROM booking.group_block_lines l
                              WHERE l.block_id = b.id), 0) AS rooms_blocked,
                   COALESCE((SELECT count(*)
                               FROM booking.reservation_units ru
                               JOIN booking.reservations r
                                 ON r.id = ru.reservation_id
                              WHERE r.group_block_id = b.id
                                AND ru.status NOT IN ('cancelled', 'no_show')
                            ), 0) AS rooms_picked_up
              FROM booking.group_blocks b
              LEFT JOIN engagement.commercial_accounts ca
                     ON ca.id = b.commercial_account_id
             WHERE b.property_id = :p
               AND (CAST(:st AS text) IS NULL OR b.status = CAST(:st AS text))
             ORDER BY b.arrival_date, b.code
             LIMIT 500
            """
        ),
        {"p": property_id, "st": status_filter},
    ).mappings().all()
    return [BlockRow(**dict(r)) for r in rows]


@group_router.get("/group-blocks/{block_id}", response_model=BlockOut)
def get_block(
    block_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    try:
        return _out(db, block_id)
    except group_blocks.BlockError as exc:
        raise HTTPException(404, str(exc)) from exc


class ReleaseIn(BaseModel):
    #: ``released`` gives back the unsold rooms and keeps the block on record;
    #: ``cancelled`` says the group fell through. Both leave pick-ups alone.
    status: str = "released"


@group_router.post("/group-blocks/{block_id}/release", response_model=BlockOut)
def release_block(
    block_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ReleaseIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Give back every room this block still holds.

    Bookings already picked up are untouched -- that is what a cut-off means.
    """
    assert_property_in_org(db, caller, property_id)
    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar_one()
    exists = db.execute(
        text("SELECT status FROM booking.group_blocks "
             "WHERE id = :b AND property_id = :p"),
        {"b": block_id, "p": property_id},
    ).mappings().first()
    if exists is None:
        raise HTTPException(404, "No such block.")
    try:
        given = group_blocks.release(
            db, block_id=block_id, property_id=property_id,
            organization_id=org, released_by=caller.user_id,
            status=body.status)
    except group_blocks.BlockError as exc:
        raise HTTPException(422, str(exc)) from exc

    record_audit(
        db, actor_subject=caller.subject, action=f"group_block.{body.status}",
        entity_type="group_block", entity_id=str(block_id),
        property_id=property_id, after={"rooms_released": given},
    )
    return _out(db, block_id)


class BlockRate(BaseModel):
    room_type_id: uuid.UUID
    nightly_rate: Decimal


class PickUpOption(BaseModel):
    """A block a booking being taken right now could draw a room from."""
    id: uuid.UUID
    code: str
    name: str
    arrival_date: date
    departure_date: date
    rooms_still_held: int
    #: The rates agreed for this block, by room type. Sent with the option so
    #: choosing the block quotes the negotiated price instead of leaving the
    #: desk to remember it -- a rate captured and never applied is the exact
    #: fault corporate rates had before 0054.
    rates: list[BlockRate] = []


@group_router.get("/properties/{property_id}/group-blocks/available",
                  response_model=list[PickUpOption])
def blocks_for_dates(
    property_id: uuid.UUID,
    arrival_date: date,
    departure_date: date,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Open blocks that still hold a room on every night of these dates.

    Offered to the booking screen so a desk taking the fourteenth name off a
    wedding list does not have to remember the block exists -- the same reason
    a corporate account now carries its rate.
    """
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            -- How many rooms this block could still supply for these exact
            -- dates. Per room type it is the *thinnest* night -- a type with
            -- three rooms free on Monday and one on Tuesday can supply one
            -- room for a Monday-to-Wednesday stay, not three -- and the
            -- block's total is those minima added up across its types.
            -- Taking the maximum instead read as a total and was not one.
            WITH per_type AS (
                SELECT n.block_id, n.room_type_id, min(n.rooms_held) AS spare
                  FROM booking.group_block_nights n
                 WHERE n.stay_date >= :arr AND n.stay_date < :dep
                 GROUP BY n.block_id, n.room_type_id
            )
            SELECT b.id, b.code, b.name, b.arrival_date, b.departure_date,
                   COALESCE(sum(t.spare), 0) AS rooms_still_held
              FROM booking.group_blocks b
              LEFT JOIN per_type t ON t.block_id = b.id
             WHERE b.property_id = :p AND b.status = 'open'
               -- The stay has to sit inside the block: a block does not hold
               -- rooms on nights it never agreed to.
               AND b.arrival_date <= :arr AND b.departure_date >= :dep
             GROUP BY b.id, b.code, b.name, b.arrival_date, b.departure_date
            HAVING COALESCE(sum(t.spare), 0) > 0
             ORDER BY b.arrival_date, b.code
            """
        ),
        {"p": property_id, "arr": arrival_date, "dep": departure_date},
    ).mappings().all()
    options = [PickUpOption(**dict(r)) for r in rows]
    if options:
        rates = db.execute(
            text(
                """
                SELECT block_id, room_type_id, nightly_rate
                  FROM booking.group_block_lines
                 WHERE block_id = ANY(:ids) AND nightly_rate IS NOT NULL
                """
            ),
            {"ids": [o.id for o in options]},
        ).mappings().all()
        by_block: dict[uuid.UUID, list[BlockRate]] = {}
        for r in rates:
            by_block.setdefault(r["block_id"], []).append(
                BlockRate(room_type_id=r["room_type_id"],
                          nightly_rate=r["nightly_rate"]))
        for o in options:
            o.rates = by_block.get(o.id, [])
    return options


# A booking joins a block by carrying ``group_block_id`` when it is created --
# see ``create_hold``. There is deliberately no endpoint for attaching an
# existing booking afterwards: that booking took its room from general
# availability, so drawing the block down for it would hand back a room the
# block never spent, and the hotel would have one more room on sale than it
# has.


# ==========================================================================
# Rooming list
#
# Who is actually in each room. A block turns into rooms; the rooming list
# turns rooms into people, and it arrives the way it arrives in real life --
# late, in a spreadsheet, with names spelled three ways.
#
# The rooms come first and the names follow. That is not a limitation, it is
# the sequence: a group books thirty rooms in March and sends the names in
# May, and a system that demands a name before it will hold a room cannot
# record what the hotel has actually agreed. So a room may sit unnamed, and
# the screen counts how many still are.
# ==========================================================================

class RoomingRow(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    reservation_number: str
    room_type_id: uuid.UUID
    room_type: str
    room_code: str | None
    arrival_date: date
    departure_date: date
    adults: int
    children: int
    status: str
    guest_id: uuid.UUID | None
    guest_name: str | None
    #: Who made the booking. Shown when the room has no name of its own, so
    #: the desk sees "under Sharma Wedding" rather than an empty cell.
    booked_by: str | None


class RoomingList(BaseModel):
    block_id: uuid.UUID
    code: str
    name: str
    rooms: list[RoomingRow]
    named: int
    unnamed: int


def _rooming(db: Session, block_id: uuid.UUID) -> RoomingList:
    b = db.execute(
        text("SELECT id, code, name FROM booking.group_blocks WHERE id = :b"),
        {"b": block_id},
    ).mappings().first()
    if b is None:
        raise HTTPException(404, "No such block.")
    rows = db.execute(
        text(
            """
            SELECT ru.id AS reservation_unit_id, ru.reservation_id,
                   r.number AS reservation_number,
                   ru.room_type_id, rt.name AS room_type,
                   rm.code AS room_code,
                   ru.arrival_date, ru.departure_date, ru.adults, ru.children,
                   ru.status, ru.guest_id,
                   g.full_name AS guest_name,
                   pg.full_name AS booked_by
              FROM booking.reservation_units ru
              JOIN booking.reservations r ON r.id = ru.reservation_id
              LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
              LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
              LEFT JOIN engagement.guests g ON g.id = ru.guest_id
              LEFT JOIN engagement.guests pg ON pg.id = r.primary_guest_id
             WHERE r.group_block_id = :b
               AND ru.status NOT IN ('cancelled', 'no_show')
             ORDER BY rt.name, r.number, ru.line_index
            """
        ),
        {"b": block_id},
    ).mappings().all()
    rooms = [RoomingRow(**dict(r)) for r in rows]
    named = sum(1 for r in rooms if r.guest_id is not None)
    return RoomingList(block_id=b["id"], code=b["code"], name=b["name"],
                       rooms=rooms, named=named, unnamed=len(rooms) - named)


@group_router.get("/group-blocks/{block_id}/rooming-list",
                  response_model=RoomingList)
def rooming_list(
    block_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Every room taken from this block, and who is in it."""
    assert_property_in_org(db, caller, property_id)
    return _rooming(db, block_id)


class NameIn(BaseModel):
    #: An existing guest, or a name to record. Exactly one -- naming a room
    #: after somebody the hotel already knows must reuse that record rather
    #: than making a second one with the same name.
    guest_id: uuid.UUID | None = None
    full_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=160)


@group_router.put("/reservation-units/{unit_id}/guest",
                  response_model=RoomingRow)
def set_room_guest(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: NameIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Say who is in this room, or clear the name.

    Sending neither a guest nor a name clears it, which puts the room back to
    "whoever booked" rather than leaving a name somebody has since withdrawn.
    """
    assert_property_in_org(db, caller, property_id)
    unit = db.execute(
        text("SELECT organization_id FROM booking.reservation_units "
             "WHERE id = :u AND property_id = :p"),
        {"u": unit_id, "p": property_id},
    ).mappings().first()
    if unit is None:
        raise HTTPException(404, "No such room on this booking.")

    guest_id = body.guest_id
    if guest_id is None and (body.full_name or "").strip():
        guest_id = uuid.uuid4()
        db.execute(
            text(
                """
                INSERT INTO engagement.guests
                    (id, organization_id, full_name, phone, email)
                VALUES (:id, :org, :name, :phone, :email)
                """
            ),
            {"id": guest_id, "org": unit["organization_id"],
             "name": body.full_name.strip(),
             "phone": (body.phone or "").strip() or None,
             "email": (body.email or "").strip() or None},
        )

    db.execute(
        text("UPDATE booking.reservation_units SET guest_id = :g, "
             "updated_at = now() WHERE id = :u"),
        {"g": guest_id, "u": unit_id},
    )
    record_audit(
        db, actor_subject=caller.subject, action="reservation_unit.guest_named",
        entity_type="reservation_unit", entity_id=str(unit_id),
        property_id=property_id,
        after={"guest_id": str(guest_id) if guest_id else None},
    )

    row = db.execute(
        text(
            """
            SELECT ru.id AS reservation_unit_id, ru.reservation_id,
                   r.number AS reservation_number,
                   ru.room_type_id, rt.name AS room_type, rm.code AS room_code,
                   ru.arrival_date, ru.departure_date, ru.adults, ru.children,
                   ru.status, ru.guest_id, g.full_name AS guest_name,
                   pg.full_name AS booked_by
              FROM booking.reservation_units ru
              JOIN booking.reservations r ON r.id = ru.reservation_id
              LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
              LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
              LEFT JOIN engagement.guests g ON g.id = ru.guest_id
              LEFT JOIN engagement.guests pg ON pg.id = r.primary_guest_id
             WHERE ru.id = :u
            """
        ),
        {"u": unit_id},
    ).mappings().one()
    return RoomingRow(**dict(row))


class ImportEntry(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=160)
    adults: int = Field(default=2, ge=1, le=10)
    children: int = Field(default=0, ge=0, le=10)


class ImportIn(BaseModel):
    room_type_id: uuid.UUID
    entries: list[ImportEntry] = Field(min_length=1, max_length=200)
    #: Defaults to the block's own dates, which is what a rooming list means
    #: unless somebody says otherwise.
    arrival_date: date | None = None
    departure_date: date | None = None
    nightly_rate: Decimal | None = Field(default=None, ge=0)


class ImportOut(BaseModel):
    reservation_id: uuid.UUID
    reservation_number: str
    rooms_created: int
    rooming: RoomingList


@group_router.post("/group-blocks/{block_id}/rooming-list",
                   response_model=ImportOut,
                   status_code=status.HTTP_201_CREATED)
def import_rooming_list(
    block_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ImportIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Turn a list of names into rooms drawn from this block.

    One booking with one room per name, not one booking per name. A group is a
    group: thirty separate reservations have to be found, changed and
    cancelled thirty times and no longer add up to anything, which is exactly
    the state this feature exists to replace.

    The rooms are drawn from the block, so they are the rooms already held --
    if the block has run out, this is refused by the same availability check
    every booking meets, and the desk is told rather than quietly sold rooms
    from general stock that the group never agreed to.

    All of it in one transaction. Half a rooming list is worse than none: the
    desk cannot tell which half went in.
    """
    assert_property_in_org(db, caller, property_id)
    block = db.execute(
        text("SELECT organization_id, arrival_date, departure_date, status, "
             "       commercial_account_id, name "
             "  FROM booking.group_blocks WHERE id = :b AND property_id = :p"),
        {"b": block_id, "p": property_id},
    ).mappings().first()
    if block is None:
        raise HTTPException(404, "No such block.")
    if block["status"] != "open":
        raise HTTPException(
            409, "This block is closed — its rooms have gone back on sale.")

    arrival = body.arrival_date or block["arrival_date"]
    departure = body.departure_date or block["departure_date"]
    if departure <= arrival:
        raise HTTPException(422, "A stay has to end after it starts.")

    # The rate the block agreed for this room type, unless the caller quoted
    # something else. Without this every imported room would be priced at rack
    # and somebody would have to correct thirty lines by hand.
    rate = body.nightly_rate
    if rate is None:
        rate = db.execute(
            text("SELECT nightly_rate FROM booking.group_block_lines "
                 " WHERE block_id = :b AND room_type_id = :rt"),
            {"b": block_id, "rt": body.room_type_id},
        ).scalar()

    try:
        result = create_hold(
            db,
            organization_id=block["organization_id"],
            property_id=property_id,
            arrival_date=arrival,
            departure_date=departure,
            lines=[HoldLine(room_type_id=body.room_type_id, units=1,
                            adults=e.adults, children=e.children,
                            nightly_rate=rate)
                   for e in body.entries],
            idempotency_key=f"rooming:{block_id}:{uuid.uuid4()}",
            hold_ttl_minutes=settings.hold_ttl_minutes,
            overbooking_allowance=settings.overbooking_allowance,
            source="group",
            company_name=block["name"],
            bill_to="company" if block["commercial_account_id"] else "guest",
            commercial_account_id=block["commercial_account_id"],
            group_block_id=block_id,
        )
    except InventoryShortage as exc:
        raise HTTPException(
            409, f"The block does not have that many rooms left: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    # A rooming list is a confirmed commitment, not a held enquiry: a held
    # booking expires, and thirty rooms silently expiring overnight is the
    # worst thing this could do. Through the ordinary confirm path, so the
    # held-to-reserved move is the same one every other booking makes rather
    # than a second implementation of it here.
    confirm_reservation(db, reservation_id=result.reservation_id)

    # Names, in the order they were given — the units come back in line order,
    # which is the order the lines were built in, which is the order of the
    # list somebody pasted.
    unit_ids = result.reservation_unit_ids or [result.reservation_unit_id]
    first_guest: uuid.UUID | None = None
    for entry, unit_id in zip(body.entries, unit_ids):
        guest_id = uuid.uuid4()
        db.execute(
            text(
                """
                INSERT INTO engagement.guests
                    (id, organization_id, full_name, phone, email)
                VALUES (:id, :org, :name, :phone, :email)
                """
            ),
            {"id": guest_id, "org": block["organization_id"],
             "name": entry.full_name.strip(),
             "phone": (entry.phone or "").strip() or None,
             "email": (entry.email or "").strip() or None},
        )
        db.execute(
            text("UPDATE booking.reservation_units SET guest_id = :g "
                 " WHERE id = :u"),
            {"g": guest_id, "u": unit_id},
        )
        first_guest = first_guest or guest_id

    # The booking itself needs somebody to talk to. The first name on the list
    # is a guess, but a better one than nobody: every screen that shows a
    # booking shows its primary guest, and a blank there reads as broken.
    if first_guest is not None:
        db.execute(
            text("UPDATE booking.reservations SET primary_guest_id = :g "
                 " WHERE id = :r AND primary_guest_id IS NULL"),
            {"g": first_guest, "r": result.reservation_id},
        )

    record_audit(
        db, actor_subject=caller.subject, action="group_block.rooming_imported",
        entity_type="group_block", entity_id=str(block_id),
        property_id=property_id,
        after={"reservation_id": str(result.reservation_id),
               "rooms": len(unit_ids)},
    )
    return ImportOut(
        reservation_id=result.reservation_id,
        reservation_number=result.number,
        rooms_created=len(unit_ids),
        rooming=_rooming(db, block_id),
    )
