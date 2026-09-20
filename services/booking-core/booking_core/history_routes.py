"""Room Status History API (screen 064).

The timeline is append-only and read-only: there is no update or delete
endpoint, because a correction to history is itself a new event. The screen
says as much, and the API agrees rather than quietly allowing edits.

``record_status_event`` is the one write path, called from wherever a room's
state genuinely changes — housekeeping, blocks, check-in/out, room edits — so
the log reflects what happened rather than what someone typed into it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import blocks_schemas as bs
from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

history_router = APIRouter(
    tags=["room-status-history"], route_class=TransactionalRoute
)

STATUS_LABELS = {
    "occupied": "Occupied", "available": "Available", "clean": "Clean",
    "dirty": "Dirty", "cleaning": "Cleaning", "inspected": "Inspected",
    "out_of_order": "Out of Order", "blocked": "Blocked",
    "inactive": "Inactive", "maintenance": "Maintenance",
}
SOURCE_LABELS = {
    "reservation_checkin": "Reservation Check-in",
    "reservation_checkout": "Reservation Check-out",
    "housekeeping": "Housekeeping",
    "maintenance": "Maintenance",
    "block": "Room Block",
    "user_update": "User Update",
    "system": "System",
}
HOUSEKEEPING_STATUSES = ("clean", "dirty", "cleaning", "inspected")


def record_status_event(
    db: Session,
    *,
    organization_id: uuid.UUID | None,
    property_id: uuid.UUID,
    room_id: uuid.UUID,
    status: str,
    source: str,
    remarks: str | None = None,
    source_reference: str | None = None,
    changed_by: uuid.UUID | None = None,
) -> None:
    """Append one status event, inside the caller's transaction.

    Deliberately forgiving: a room's history must never be the reason a
    check-in or a block fails, so an unknown status is skipped rather than
    raising into the caller's flow.
    """
    if status not in STATUS_LABELS or source not in SOURCE_LABELS:
        return
    db.execute(
        text(
            """
            INSERT INTO operations.room_status_events
                (organization_id, property_id, room_id, status, source,
                 source_reference, remarks, changed_by)
            VALUES (:org, :prop, :room, :status, :source, :ref, :remarks, :actor)
            """
        ),
        {
            "org": organization_id, "prop": property_id, "room": room_id,
            "status": status, "source": source, "ref": source_reference,
            "remarks": (remarks or None), "actor": changed_by,
        },
    )


def _day(value: date) -> str:
    """"08 Sep 2026" — the date format the rest of the screens use."""
    return value.strftime("%d %b %Y")


def _since(events, current: str) -> datetime | None:
    """When the room entered its current status.

    Walk back through the run of consecutive events with the current status and
    return the earliest of them, so "Since" reads as the start of the current
    spell rather than the timestamp of the last thing that happened.
    """
    since = None
    for row in events:  # newest first
        if row["status"] != current:
            break
        since = row["occurred_at"]
    return since or (events[0]["occurred_at"] if events else None)


class StatusEventOut(BaseModel):
    id: uuid.UUID
    status: str
    status_label: str
    source: str
    source_label: str
    source_reference: str | None = None
    remarks: str | None = None
    changed_by_name: str | None = None
    changed_by_role: str | None = None
    occurred_at: datetime


class LinkedRecord(BaseModel):
    kind: str
    reference: str
    detail: str | None = None


class RoomStatusSummary(BaseModel):
    """The right-hand rail on screen 064."""

    room_id: uuid.UUID
    room_code: str
    room_type_name: str
    floor: str | None = None
    max_occupancy: int
    current_status: str
    current_status_label: str
    since: datetime | None = None
    current_guest: str | None = None
    reservation_number: str | None = None
    reservation_state: str | None = None
    total_changes: int = 0
    created_at: datetime | None = None
    last_updated_at: datetime | None = None
    primary_photo_url: str | None = None
    linked_records: list[LinkedRecord] = Field(default_factory=list)


class HousekeepingStatusIn(BaseModel):
    """Housekeeping is the one status a user sets by hand on this screen."""

    status: str
    remarks: str | None = Field(default=None, max_length=500)


@history_router.get(
    "/rooms/{room_id}/status-history", response_model=list[StatusEventOut]
)
def status_history(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    status: str | None = None,
    changed_by: uuid.UUID | None = None,
    source: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The timeline, newest first."""
    assert_property_in_org(db, caller, property_id)
    where = ["e.room_id = :room", "e.property_id = :prop"]
    params: dict[str, Any] = {"room": room_id, "prop": property_id, "limit": limit}
    if date_from:
        where.append("e.occurred_at >= :from")
        params["from"] = date_from
    if date_to:
        # Inclusive of the whole end day.
        where.append("e.occurred_at < (CAST(:to AS date) + 1)")
        params["to"] = date_to
    if status:
        where.append("e.status = :status")
        params["status"] = status
    if source:
        where.append("e.source = :source")
        params["source"] = source
    if changed_by:
        where.append("e.changed_by = :actor")
        params["actor"] = changed_by

    rows = db.execute(
        text(
            f"""
            SELECT e.id, e.status, e.source, e.source_reference, e.remarks,
                   e.occurred_at,
                   u.display_name AS changed_by_name,
                   rl.name AS changed_by_role
            FROM operations.room_status_events e
            LEFT JOIN iam.users u ON u.id = e.changed_by
            LEFT JOIN LATERAL (
                -- The role the actor holds at this property, else in the org.
                SELECT ro.name
                FROM iam.memberships m
                JOIN iam.role_assignments ra ON ra.membership_id = m.id
                JOIN iam.roles ro ON ro.id = ra.role_id
                WHERE m.user_id = e.changed_by
                  AND m.organization_id = e.organization_id
                ORDER BY (ra.property_id = e.property_id) DESC NULLS LAST
                LIMIT 1
            ) rl ON true
            WHERE {' AND '.join(where)}
            ORDER BY e.occurred_at DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    return [
        StatusEventOut(
            **r,
            status_label=STATUS_LABELS.get(r["status"], r["status"]),
            source_label=SOURCE_LABELS.get(r["source"], r["source"]),
        )
        for r in rows
    ]


@history_router.get(
    "/rooms/{room_id}/status-summary", response_model=RoomStatusSummary
)
def status_summary(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Current status, how long it has held, and the records behind it."""
    assert_property_in_org(db, caller, property_id)
    room = db.execute(
        text(
            """
            SELECT r.id, r.code, r.floor, r.created_at, r.updated_at,
                   rt.name AS room_type_name, rt.max_occupancy,
                   COALESCE(rc.cleanliness, 'clean') AS cleanliness,
                   r.status, r.service_status
            FROM property.rooms r
            JOIN property.room_types rt ON rt.id = r.room_type_id
            LEFT JOIN operations.room_condition rc ON rc.room_id = r.id
            WHERE r.id = :id AND r.property_id = :prop
            """
        ),
        {"id": room_id, "prop": property_id},
    ).mappings().first()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    events = db.execute(
        text(
            "SELECT status, occurred_at FROM operations.room_status_events "
            "WHERE room_id = :id ORDER BY occurred_at DESC LIMIT 100"
        ),
        {"id": room_id},
    ).mappings().all()
    total = db.execute(
        text("SELECT count(*) FROM operations.room_status_events WHERE room_id = :id"),
        {"id": room_id},
    ).scalar_one()

    # Who is in the room, or due in it.
    occ = db.execute(
        text(
            """
            SELECT res.number AS reservation_number, g.full_name,
                   (s.id IS NOT NULL) AS in_house
            FROM booking.reservation_units ru
            LEFT JOIN booking.stays s
              ON s.reservation_unit_id = ru.id AND s.status = 'in_house'
            LEFT JOIN booking.reservations res ON res.id = ru.reservation_id
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

    linked: list[LinkedRecord] = []
    if occ and occ["reservation_number"]:
        linked.append(
            LinkedRecord(
                kind="reservation", reference=occ["reservation_number"],
                detail="In-house" if occ["in_house"] else "Arriving",
            )
        )
    linked.append(
        LinkedRecord(
            kind="housekeeping", reference=room["cleanliness"].title(),
            detail="Current housekeeping state",
        )
    )
    blk = db.execute(
        text(
            """
            SELECT b.group_id, b.block_type, b.reason_category,
                   b.start_date, b.end_date, b.linked_reference
            FROM booking.room_blocks b
            WHERE b.room_id = :id AND b.status = 'active'
              AND CURRENT_DATE BETWEEN b.start_date AND b.end_date
            LIMIT 1
            """
        ),
        {"id": room_id},
    ).mappings().first()
    if blk:
        linked.append(
            LinkedRecord(
                kind="maintenance",
                reference=blk["linked_reference"]
                or bs.REASON_CATEGORIES.get(
                    blk["reason_category"], blk["reason_category"]
                ),
                detail=f"{_day(blk['start_date'])} to {_day(blk['end_date'])}",
            )
        )

    # Current status mirrors the Rooms grid's precedence so the two agree.
    if blk:
        current = "out_of_order" if blk["block_type"] == "out_of_order" else "blocked"
    elif occ and occ["in_house"]:
        current = "occupied"
    elif room["status"] != "active":
        current = "inactive"
    elif room["service_status"] != "in_service":
        current = "out_of_order"
    else:
        current = room["cleanliness"]

    photo = db.execute(
        text(
            """
            SELECT url, storage_key FROM property.room_photos
             WHERE room_id = :id AND is_primary
            UNION ALL
            SELECT tp.url, tp.storage_key FROM property.room_type_photos tp
             JOIN property.rooms r ON r.room_type_id = tp.room_type_id
            WHERE r.id = :id AND tp.is_primary
              AND NOT EXISTS (SELECT 1 FROM property.room_photos
                               WHERE room_id = :id AND is_primary)
            LIMIT 1
            """
        ),
        {"id": room_id},
    ).mappings().first()
    from .rooms_routes import photo_url  # local import avoids a cycle at import time

    return RoomStatusSummary(
        room_id=room["id"], room_code=room["code"],
        room_type_name=room["room_type_name"], floor=room["floor"],
        max_occupancy=room["max_occupancy"],
        current_status=current,
        current_status_label=STATUS_LABELS.get(current, current),
        since=_since(events, current),
        current_guest=occ["full_name"] if occ else None,
        reservation_number=occ["reservation_number"] if occ else None,
        reservation_state=("In-house" if occ and occ["in_house"] else "Arriving")
        if occ and occ["reservation_number"] else None,
        total_changes=total,
        created_at=room["created_at"], last_updated_at=room["updated_at"],
        primary_photo_url=photo_url(photo["url"], photo["storage_key"]) if photo else None,
        linked_records=linked,
    )


@history_router.post(
    "/rooms/{room_id}/housekeeping-status",
    response_model=RoomStatusSummary,
    status_code=http_status.HTTP_200_OK,
)
def set_housekeeping_status(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    body: HousekeepingStatusIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Set the housekeeping state and record it on the timeline.

    History itself is read-only; this changes the room's *current* state, and
    the event is the consequence.
    """
    assert_property_in_org(db, caller, property_id)
    if body.status not in HOUSEKEEPING_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid housekeeping status. Use one of: "
                   f"{', '.join(HOUSEKEEPING_STATUSES)}",
        )
    owns = db.execute(
        text("SELECT 1 FROM property.rooms WHERE id = :id AND property_id = :prop"),
        {"id": room_id, "prop": property_id},
    ).first()
    if owns is None:
        raise HTTPException(status_code=404, detail="Room not found")

    db.execute(
        text(
            """
            INSERT INTO operations.room_condition
                (room_id, organization_id, property_id, cleanliness, updated_at)
            VALUES (:id, :org, :prop, :status, now())
            ON CONFLICT (room_id) DO UPDATE
                SET cleanliness = EXCLUDED.cleanliness,
                    updated_at = now(),
                    version = operations.room_condition.version + 1
            """
        ),
        {
            "id": room_id, "org": caller.organization_id, "prop": property_id,
            "status": body.status,
        },
    )
    record_status_event(
        db, organization_id=caller.organization_id, property_id=property_id,
        room_id=room_id, status=body.status, source="housekeeping",
        remarks=body.remarks, changed_by=caller.user_id,
    )
    return status_summary(room_id, property_id, caller, db)
