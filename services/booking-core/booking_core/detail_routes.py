"""Room Details API (screen 011).

The read-only counterpart to the room editor (059): one room, everything known
about it, with the tabs the screen offers backed by the records that already
exist elsewhere — reservations, blocks, and the status timeline from 064.

Nothing here writes. The screen's three actions (Block Room, Mark Out of Order,
Edit Room) are the existing endpoints on 063 and 059; this module only supplies
what those actions need to be offered accurately, notably ``version`` for the
optimistic-locking check.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import blocks_schemas as bs
from . import rooms_schemas as rs
from .database import get_session
from .rooms_routes import (
    _BLOCK_JOIN,
    _OCCUPANCY_SQL,
    _OCCUPANT_JOIN,
    _amenities_for_rooms,
    _photo_rows,
    _photos_out,
)
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

detail_router = APIRouter(tags=["room-details"], route_class=TransactionalRoute)

OCCUPANCY_LABELS = {
    "occupied": "Occupied", "reserved": "Reserved", "available": "Available",
    "blocked": "Blocked", "out_of_service": "Out of Order",
    "maintenance": "Maintenance", "inactive": "Inactive",
}
# The mockup calls a clean room "Ready"; the other states keep their own names.
HOUSEKEEPING_LABELS = {
    "clean": "Ready", "dirty": "Dirty", "cleaning": "Cleaning",
    "inspected": "Inspected",
}
RESERVATION_LABELS = {
    "reserved": "Confirmed", "checked_in": "In-house", "checked_out": "Checked out",
    "cancelled": "Cancelled", "no_show": "No show",
}
# Words in a room type name that read as a tag on their own.
_TYPE_TAGS = ("Deluxe", "Premium", "Standard", "Suite", "Villa", "Executive")


class UpcomingBooking(BaseModel):
    """The next stay this room is committed to, in-house or arriving."""

    reservation_id: uuid.UUID
    reservation_number: str
    guest_name: str | None = None
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    status: str
    status_label: str
    in_house: bool


class RoomOverviewOut(BaseModel):
    id: uuid.UUID
    code: str
    room_type_id: uuid.UUID
    room_type_name: str
    building_name: str | None = None
    floor_name: str | None = None
    bed_setup: str | None = None
    view_type: str | None = None
    size_sqft: int | None = None
    max_adults: int
    max_children: int
    base_rate: Decimal | None = None
    housekeeping_zone: str | None = None
    accessibility: str
    near_elevator: bool
    notes: str | None = None
    status: str
    service_status: str
    occupancy_state: str
    occupancy_label: str
    housekeeping_state: str
    housekeeping_label: str
    version: int
    tags: list[str] = Field(default_factory=list)
    photos: list[rs.RoomPhotoOut] = Field(default_factory=list)
    amenities: list[rs.RoomAmenityOut] = Field(default_factory=list)
    upcoming_booking: UpcomingBooking | None = None
    active_block_group_id: uuid.UUID | None = None


class RoomReservationOut(BaseModel):
    """One row on the Reservations tab."""

    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    reservation_number: str
    guest_name: str | None = None
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    status: str
    status_label: str


class RoomMaintenanceOut(BaseModel):
    """One row on the Maintenance tab — a block held against this room."""

    group_id: uuid.UUID
    block_type: str
    block_type_label: str
    reason_category: str
    reason_category_label: str
    reason: str | None = None
    severity: str
    start_date: date
    end_date: date
    status: str
    linked_reference: str | None = None
    created_by_name: str | None = None
    created_at: datetime | None = None


def _tags(row) -> list[str]:
    """Derive the room's tags from what is already known about it.

    The mockup shows tags but offers no way to edit them, so inventing a column
    (and a second place for the truth to live) would be worse than reading them
    off the room's own attributes. "Sea View / Deluxe / Family Friendly" in the
    design falls out of view type, room type and child capacity exactly.
    """
    tags: list[str] = []
    if row["view_type"]:
        tags.append(row["view_type"])
    for word in _TYPE_TAGS:
        if word.lower() in (row["room_type_name"] or "").lower():
            tags.append(word)
            break
    if row["max_children"] > 0:
        tags.append("Family Friendly")
    if row["accessibility"] and row["accessibility"] != "none":
        tags.append("Accessible")
    if row["near_elevator"]:
        tags.append("Near Elevator")
    return tags


def _room_or_404(db: Session, room_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text("SELECT 1 FROM property.rooms WHERE id = :id AND property_id = :prop"),
        {"id": room_id, "prop": property_id},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Room not found")


@detail_router.get("/rooms/{room_id}/overview", response_model=RoomOverviewOut)
def room_overview(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Everything screen 011 shows above the tabs."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            f"""
            SELECT r.id, r.code, r.room_type_id, rt.name AS room_type_name,
                   rt.size_sqft,
                   COALESCE(b.name, r.building)  AS building_name,
                   COALESCE(f.name, r.floor)     AS floor_name,
                   r.bed_setup, r.view_type, r.max_adults, r.max_children,
                   COALESCE(r.base_rate, rt.base_rate) AS base_rate,
                   r.housekeeping_zone, r.accessibility, r.near_elevator,
                   r.notes, r.status, r.service_status, r.version,
                   {_OCCUPANCY_SQL} AS occupancy_state,
                   COALESCE(rc.cleanliness, 'clean') AS housekeeping_state,
                   blk.group_id AS active_block_group_id
            FROM property.rooms r
            JOIN property.room_types rt
              ON rt.id = r.room_type_id AND rt.property_id = r.property_id
            LEFT JOIN property.buildings b ON b.id = r.building_id
            LEFT JOIN property.floors f    ON f.id = r.floor_id
            LEFT JOIN operations.room_condition rc ON rc.room_id = r.id
            {_OCCUPANT_JOIN}
            {_BLOCK_JOIN}
            WHERE r.id = :id AND r.property_id = :prop
              AND r.organization_id = :org
            """
        ),
        {"id": room_id, "prop": property_id, "org": caller.organization_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Room not found")

    # Room photos if it has its own, else the room type's — same fallback the
    # inventory cards use, so the gallery is never empty for a configured type.
    photos = _photos_out(_photo_rows(db, "room_photos", "room_id", room_id))
    if not photos:
        photos = _photos_out(
            _photo_rows(db, "room_type_photos", "room_type_id", row["room_type_id"])
        )

    booking = db.execute(
        text(
            """
            SELECT ru.reservation_id, res.number AS reservation_number,
                   g.full_name AS guest_name,
                   ru.arrival_date, ru.departure_date, ru.adults, ru.children,
                   ru.status,
                   (s.id IS NOT NULL) AS in_house
            FROM booking.reservation_units ru
            JOIN booking.reservations res ON res.id = ru.reservation_id
            LEFT JOIN booking.stays s
              ON s.reservation_unit_id = ru.id AND s.status = 'in_house'
            LEFT JOIN engagement.guests g ON g.id = res.primary_guest_id
            WHERE ru.assigned_room_id = :id
              AND ru.status NOT IN ('cancelled', 'checked_out', 'no_show')
              AND ru.departure_date >= CURRENT_DATE
            ORDER BY (s.id IS NOT NULL) DESC, ru.arrival_date
            LIMIT 1
            """
        ),
        {"id": room_id},
    ).mappings().first()

    upcoming = None
    if booking:
        upcoming = UpcomingBooking(
            **{k: booking[k] for k in (
                "reservation_id", "reservation_number", "guest_name",
                "arrival_date", "departure_date", "adults", "children",
                "status", "in_house")},
            nights=(booking["departure_date"] - booking["arrival_date"]).days,
            status_label=RESERVATION_LABELS.get(booking["status"], booking["status"]),
        )

    data = dict(row)
    return RoomOverviewOut(
        **data,
        occupancy_label=OCCUPANCY_LABELS.get(
            data["occupancy_state"], data["occupancy_state"]
        ),
        housekeeping_label=HOUSEKEEPING_LABELS.get(
            data["housekeeping_state"], data["housekeeping_state"]
        ),
        tags=_tags(row),
        photos=photos,
        amenities=_amenities_for_rooms(db, [room_id]).get(room_id, []),
        upcoming_booking=upcoming,
    )


@detail_router.get(
    "/rooms/{room_id}/reservations", response_model=list[RoomReservationOut]
)
def room_reservations(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The Reservations tab: every stay assigned to this room, newest first."""
    assert_property_in_org(db, caller, property_id)
    _room_or_404(db, room_id, property_id)
    rows = db.execute(
        text(
            """
            SELECT ru.id AS reservation_unit_id, ru.reservation_id,
                   res.number AS reservation_number, g.full_name AS guest_name,
                   ru.arrival_date, ru.departure_date, ru.adults, ru.children,
                   ru.status
            FROM booking.reservation_units ru
            JOIN booking.reservations res ON res.id = ru.reservation_id
            LEFT JOIN engagement.guests g ON g.id = res.primary_guest_id
            WHERE ru.assigned_room_id = :id AND ru.property_id = :prop
            ORDER BY ru.arrival_date DESC
            LIMIT :limit
            """
        ),
        {"id": room_id, "prop": property_id, "limit": limit},
    ).mappings().all()
    return [
        RoomReservationOut(
            **r,
            nights=(r["departure_date"] - r["arrival_date"]).days,
            status_label=RESERVATION_LABELS.get(r["status"], r["status"]),
        )
        for r in rows
    ]


@detail_router.get(
    "/rooms/{room_id}/maintenance", response_model=list[RoomMaintenanceOut]
)
def room_maintenance(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The Maintenance tab: blocks held against this room, newest first."""
    assert_property_in_org(db, caller, property_id)
    _room_or_404(db, room_id, property_id)
    rows = db.execute(
        text(
            """
            SELECT bl.group_id, bl.block_type, bl.reason_category, bl.reason,
                   bl.severity, bl.start_date, bl.end_date, bl.status,
                   bl.linked_reference, bl.created_at,
                   u.display_name AS created_by_name
            FROM booking.room_blocks bl
            LEFT JOIN iam.users u ON u.id = bl.created_by
            WHERE bl.room_id = :id AND bl.property_id = :prop
            ORDER BY bl.start_date DESC, bl.created_at DESC
            LIMIT :limit
            """
        ),
        {"id": room_id, "prop": property_id, "limit": limit},
    ).mappings().all()
    return [
        RoomMaintenanceOut(
            **r,
            block_type_label={
                "room_block": "Room Block", "out_of_order": "Out of Order",
            }.get(r["block_type"], r["block_type"]),
            reason_category_label=bs.REASON_CATEGORIES.get(
                r["reason_category"], r["reason_category"]
            ),
        )
        for r in rows
    ]
