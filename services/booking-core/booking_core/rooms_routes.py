"""Rooms, Room Types and Amenities API (screens 008 / 059 / 060 / 062).

Every route is deny-by-default: the caller needs an active membership and a
scoped ``rooms.*`` permission, and the target property must belong to the
caller's organization (cross-tenant reads and writes are rejected, not merely
filtered). Mutations write an append-only ``iam.audit_events`` row in the same
transaction as the change, and use optimistic locking on ``version`` so a stale
editor gets a 409 rather than silently overwriting a concurrent edit.

Operational state (occupied / cleaning / maintenance) is *derived* at query
time from the booking and housekeeping tables rather than stored on the room,
so it can never drift out of sync with the reservation data that owns it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.objectstore import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_BYTES,
    ObjectStoreConfig,
    ObjectStoreError,
    build_key,
    delete_object,
    presigned_url,
    put_object,
    put_thumbnail,
)
from chirala_common.routing import TransactionalRoute
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import rooms_schemas as rs
from .database import get_session
from .branding import UnusableColour, theme, validate_colour
from .inventory import active_room_count, capacity_shortfall, sync_capacity
from .history_routes import record_status_event
from .settings import settings

get_caller, require_permission, _require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

rooms_router = APIRouter(tags=["rooms"], route_class=TransactionalRoute)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _validate_choice(value: str | None, allowed: tuple[str, ...], field: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field}: {value!r}. Allowed: {', '.join(allowed)}",
        )


def _conflict(entity: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"{entity} was modified by someone else. Reload and try again.",
    )


# Derived operational state, in priority order. An *active block covering
# today* is the authority on whether a room is sellable — `service_status` is
# only a physical description and is kept in step with it, never consulted
# ahead of it. Otherwise a room is occupied when an in-house stay is attached
# to a reservation unit assigned to it.
_OCCUPANCY_SQL = """
    CASE
        WHEN blk.block_type = 'out_of_order'     THEN 'out_of_service'
        WHEN blk.block_type = 'room_block'       THEN 'blocked'
        WHEN r.status <> 'active'                THEN 'inactive'
        WHEN occ.stay_id IS NOT NULL             THEN 'occupied'
        WHEN occ.covers_today                    THEN 'reserved'
        WHEN r.service_status = 'out_of_service' THEN 'out_of_service'
        WHEN r.service_status = 'maintenance'    THEN 'maintenance'
        ELSE 'available'
    END
"""

# Today's active block on each room, if any.
_BLOCK_JOIN = """
    LEFT JOIN LATERAL (
        SELECT b.group_id, b.block_type, b.reason_category, b.reason,
               b.start_date, b.end_date
        FROM booking.room_blocks b
        WHERE b.room_id = r.id AND b.status = 'active'
          AND CURRENT_DATE BETWEEN b.start_date AND b.end_date
        ORDER BY b.block_type
        LIMIT 1
    ) blk ON TRUE
"""

# The room's current or next occupant. An in-house stay wins; otherwise the
# earliest assigned reservation that has not departed. Assignment alone is
# enough to appear here — a room held for an arriving guest is not "available",
# and the card must say so rather than looking free until check-in.
_OCCUPANT_JOIN = """
    LEFT JOIN LATERAL (
        SELECT s.id AS stay_id, ru.arrival_date, ru.departure_date,
               g.full_name,
               (CURRENT_DATE >= ru.arrival_date
                AND CURRENT_DATE < ru.departure_date) AS covers_today
        FROM booking.reservation_units ru
        LEFT JOIN booking.stays s
          ON s.reservation_unit_id = ru.id AND s.status = 'in_house'
        LEFT JOIN booking.reservations res ON res.id = ru.reservation_id
        LEFT JOIN engagement.guests g ON g.id = res.primary_guest_id
        WHERE ru.assigned_room_id = r.id
          AND ru.status NOT IN ('cancelled', 'checked_out', 'no_show')
          AND ru.departure_date >= CURRENT_DATE
        ORDER BY (s.id IS NOT NULL) DESC, ru.arrival_date
        LIMIT 1
    ) occ ON TRUE
"""

_PHOTO_JOIN = """
    LEFT JOIN LATERAL (
        SELECT url, storage_key FROM property.room_photos
         WHERE room_id = r.id AND is_primary
        UNION ALL
        SELECT url, storage_key FROM property.room_type_photos
         WHERE room_type_id = r.room_type_id AND is_primary
           AND NOT EXISTS (
             SELECT 1 FROM property.room_photos
              WHERE room_id = r.id AND is_primary
           )
        LIMIT 1
    ) pic ON TRUE
"""


def _amenities_for_rooms(
    db: Session, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[rs.RoomAmenityOut]]:
    """Fetch amenities for many rooms in one query (avoids N+1 on the grid)."""
    if not room_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT ra.room_id, a.id, a.code, a.name, a.icon, a.category
            FROM property.room_amenities ra
            JOIN property.amenities a ON a.id = ra.amenity_id
            WHERE ra.room_id = ANY(:ids) AND a.status = 'active'
            ORDER BY a.category, a.name
            """
        ),
        {"ids": room_ids},
    ).all()
    out: dict[uuid.UUID, list[rs.RoomAmenityOut]] = {}
    for row in rows:
        out.setdefault(row.room_id, []).append(
            rs.RoomAmenityOut(
                id=row.id, code=row.code, name=row.name, icon=row.icon,
                category=row.category,
            )
        )
    return out


def _replace_room_amenities(
    db: Session, *, room_id: uuid.UUID, property_id: uuid.UUID,
    amenity_ids: list[uuid.UUID],
) -> None:
    """Replace a room's amenity set. Composite FK rejects cross-property ids."""
    db.execute(
        text("DELETE FROM property.room_amenities WHERE room_id = :rid"),
        {"rid": room_id},
    )
    for amenity_id in dict.fromkeys(amenity_ids):
        db.execute(
            text(
                """
                INSERT INTO property.room_amenities (room_id, amenity_id, property_id)
                VALUES (:rid, :aid, :prop)
                """
            ),
            {"rid": room_id, "aid": amenity_id, "prop": property_id},
        )


def _room_snapshot(db: Session, room_id: uuid.UUID) -> dict[str, Any] | None:
    """Flat before/after snapshot for the audit trail."""
    row = db.execute(
        text(
            """
            SELECT code, room_type_id, building, floor, bed_setup, view_type,
                   max_adults, max_children, base_rate, housekeeping_zone,
                   accessibility, near_elevator, status, service_status, version
            FROM property.rooms WHERE id = :id
            """
        ),
        {"id": room_id},
    ).mappings().first()
    if row is None:
        return None
    return {k: _jsonable(v) for k, v in row.items()}


def _jsonable(value: Any) -> Any:
    """Coerce a DB value to something ``json.dumps`` accepts.

    Snapshots go straight into the audit row's jsonb column, so UUIDs, Decimals
    and dates have to be stringified first.
    """
    if isinstance(value, (uuid.UUID, Decimal, date, datetime)):
        return str(value)
    return value


# --------------------------------------------------------------------------
# Screen 008 — Rooms & Villas Inventory
# --------------------------------------------------------------------------
@rooms_router.get("/rooms", response_model=rs.RoomListOut)
def list_rooms(
    property_id: uuid.UUID,
    floor: str | None = None,
    building: str | None = None,
    room_type_id: uuid.UUID | None = None,
    state: str | None = Query(
        default=None,
        description="Derived state filter: available|occupied|cleaning|maintenance|out_of_service",
    ),
    search: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Room inventory grid + KPI cards, filtered server-side."""
    assert_property_in_org(db, caller, property_id)

    where = ["r.property_id = :prop", "r.organization_id = :org"]
    params: dict[str, Any] = {
        "prop": property_id, "org": caller.organization_id,
        "limit": limit, "offset": offset,
    }
    if floor:
        where.append("r.floor = :floor")
        params["floor"] = floor
    if building:
        where.append("r.building = :building")
        params["building"] = building
    if room_type_id:
        where.append("r.room_type_id = :rtid")
        params["rtid"] = room_type_id
    if search:
        where.append("(r.code ILIKE :q OR rt.name ILIKE :q)")
        params["q"] = f"%{search}%"

    base = f"""
        FROM property.rooms r
        JOIN property.room_types rt
          ON rt.id = r.room_type_id AND rt.property_id = r.property_id
        LEFT JOIN operations.room_condition rc ON rc.room_id = r.id
        {_OCCUPANT_JOIN}
        {_PHOTO_JOIN}
        {_BLOCK_JOIN}
        WHERE {' AND '.join(where)}
    """

    # The derived state is not a column, so it filters in an outer wrapper.
    state_filter = ""
    if state:
        if state == "cleaning":
            # "Cleaning" is a housekeeping state, not an occupancy one.
            state_filter = (
                "WHERE housekeeping_state IN ('dirty','cleaning') "
                "AND occupancy_state = 'available'"
            )
        else:
            state_filter = "WHERE occupancy_state = :state"
            params["state"] = state

    select_sql = f"""
        SELECT * FROM (
            SELECT r.id, r.code, r.room_type_id, rt.name AS room_type_name,
                   r.building_id, r.floor_id, r.building, r.floor,
                   r.bed_setup, r.view_type,
                   r.max_adults, r.max_children,
                   COALESCE(r.base_rate, rt.base_rate) AS base_rate,
                   r.status, r.service_status, r.housekeeping_zone,
                   r.accessibility, r.near_elevator, r.version,
                   {_OCCUPANCY_SQL} AS occupancy_state,
                   COALESCE(rc.cleanliness, 'clean') AS housekeeping_state,
                   occ.full_name AS guest_name,
                   occ.arrival_date, occ.departure_date,
                   pic.url AS pic_url, pic.storage_key AS pic_key,
                   blk.group_id AS block_group_id, blk.reason_category AS block_reason,
                   blk.start_date AS block_start, blk.end_date AS block_end
            {base}
        ) q
        {state_filter}
        ORDER BY q.floor NULLS LAST, q.code
        LIMIT :limit OFFSET :offset
    """
    rows = db.execute(text(select_sql), params).mappings().all()

    count_sql = f"SELECT count(*) FROM ( SELECT {_OCCUPANCY_SQL} AS occupancy_state, COALESCE(rc.cleanliness,'clean') AS housekeeping_state {base} ) q {state_filter}"
    total = db.execute(text(count_sql), params).scalar_one()

    amenities = _amenities_for_rooms(db, [r["id"] for r in rows])
    items = []
    for r in rows:
        row = dict(r)
        # Presign whichever photo the lateral picked (room's own, else its type's).
        row["primary_photo_url"] = photo_url(row.pop("pic_url"), row.pop("pic_key"))
        items.append(rs.RoomListItem(**row, amenities=amenities.get(r["id"], [])))

    return rs.RoomListOut(items=items, total=total, stats=_room_stats(db, property_id, caller))


def _room_stats(db: Session, property_id: uuid.UUID, caller: Caller) -> rs.RoomStatsOut:
    row = db.execute(
        text(
            f"""
            SELECT
              count(*) AS total_units,
              count(*) FILTER (WHERE st = 'occupied')       AS occupied,
              count(*) FILTER (WHERE st = 'available'
                               AND hk IN ('clean','inspected')) AS available,
              count(*) FILTER (WHERE st = 'available'
                               AND hk IN ('dirty','cleaning'))  AS cleaning,
              count(*) FILTER (WHERE st IN ('maintenance','out_of_service')) AS maintenance
            FROM (
                SELECT {_OCCUPANCY_SQL} AS st,
                       COALESCE(rc.cleanliness, 'clean') AS hk
                FROM property.rooms r
                LEFT JOIN operations.room_condition rc ON rc.room_id = r.id
                {_OCCUPANT_JOIN}
                {_BLOCK_JOIN}
                WHERE r.property_id = :prop AND r.organization_id = :org
            ) s
            """
        ),
        {"prop": property_id, "org": caller.organization_id},
    ).mappings().one()
    total = row["total_units"] or 0
    return rs.RoomStatsOut(
        total_units=total,
        occupied=row["occupied"],
        available=row["available"],
        cleaning=row["cleaning"],
        maintenance=row["maintenance"],
        occupancy_pct=round(100.0 * row["occupied"] / total, 1) if total else 0.0,
        available_pct=round(100.0 * row["available"] / total, 1) if total else 0.0,
    )


@rooms_router.get("/rooms/facets", response_model=rs.RoomFacetsOut)
def room_facets(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Filter dropdown options for screens 008 and 059, derived from live data."""
    assert_property_in_org(db, caller, property_id)
    p = {"prop": property_id}

    def _distinct(column: str) -> list[str]:
        return [
            r[0]
            for r in db.execute(
                text(
                    f"SELECT DISTINCT {column} FROM property.rooms "
                    f"WHERE property_id = :prop AND {column} IS NOT NULL "
                    f"ORDER BY 1"
                ),
                p,
            ).all()
        ]

    return rs.RoomFacetsOut(
        floors=_distinct("floor"),
        buildings=_distinct("building"),
        housekeeping_zones=_distinct("housekeeping_zone"),
        room_types=_list_room_types(db, property_id),
    )


@rooms_router.get("/rooms/{room_id}", response_model=rs.RoomDetailOut)
def get_room(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Load one room for the Add/Edit Room screen (059)."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            f"""
            SELECT r.id, r.code, r.room_type_id, rt.name AS room_type_name,
                   r.building_id, r.floor_id, r.building, r.floor,
                   r.bed_setup, r.view_type,
                   r.max_adults, r.max_children,
                   COALESCE(r.base_rate, rt.base_rate) AS base_rate,
                   r.status, r.service_status, r.housekeeping_zone,
                   r.accessibility, r.near_elevator, r.version, r.notes,
                   r.active_from, r.retired_on, r.created_at, r.updated_at,
                   {_OCCUPANCY_SQL} AS occupancy_state,
                   COALESCE(rc.cleanliness, 'clean') AS housekeeping_state,
                   occ.full_name AS guest_name, occ.arrival_date, occ.departure_date,
                   pic.url AS pic_url, pic.storage_key AS pic_key,
                   blk.group_id AS block_group_id, blk.reason_category AS block_reason,
                   blk.start_date AS block_start, blk.end_date AS block_end
            FROM property.rooms r
            JOIN property.room_types rt
              ON rt.id = r.room_type_id AND rt.property_id = r.property_id
            LEFT JOIN operations.room_condition rc ON rc.room_id = r.id
            {_OCCUPANT_JOIN}
            {_PHOTO_JOIN}
            {_BLOCK_JOIN}
            WHERE r.id = :id AND r.property_id = :prop
              AND r.organization_id = :org
            """
        ),
        {"id": room_id, "prop": property_id, "org": caller.organization_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Room not found")

    photos = _photos_out(_photo_rows(db, "room_photos", "room_id", room_id))
    amenities = _amenities_for_rooms(db, [room_id]).get(room_id, [])
    detail_row = dict(row)
    detail_row["primary_photo_url"] = photo_url(
        detail_row.pop("pic_url"), detail_row.pop("pic_key")
    )
    return rs.RoomDetailOut(**detail_row, amenities=amenities, photos=photos)


@rooms_router.post(
    "/rooms", response_model=rs.RoomDetailOut, status_code=status.HTTP_201_CREATED
)
def create_room(
    body: rs.RoomIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    """Create a room (screen 059, Add mode)."""
    assert_property_in_org(db, caller, property_id)
    _validate_choice(body.status, rs.ROOM_STATUSES, "status")
    _validate_choice(body.service_status, rs.SERVICE_STATUSES, "service_status")
    _validate_choice(body.accessibility, rs.ACCESSIBILITY, "accessibility")

    # The room type must live in this property (composite FK also enforces it,
    # but this gives a clear 422 instead of a constraint error).
    owns_type = db.execute(
        text(
            "SELECT 1 FROM property.room_types WHERE id = :rt AND property_id = :prop"
        ),
        {"rt": body.room_type_id, "prop": property_id},
    ).first()
    if owns_type is None:
        raise HTTPException(status_code=422, detail="Room type not in this property")

    room_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.rooms
                    (id, organization_id, property_id, room_type_id, code,
                     building, floor, bed_setup, view_type, max_adults,
                     max_children, base_rate, housekeeping_zone, accessibility,
                     near_elevator, status, service_status, notes, active_from)
                VALUES
                    (:id, :org, :prop, :rt, :code, :building, :floor, :bed,
                     :view, :ad, :ch, :rate, :zone, :acc, :elev, :status,
                     :svc, :notes, :active_from)
                """
            ),
            {
                "id": room_id, "org": caller.organization_id, "prop": property_id,
                "rt": body.room_type_id, "code": body.code,
                "building": body.building, "floor": body.floor,
                "bed": body.bed_setup, "view": body.view_type,
                "ad": body.max_adults, "ch": body.max_children,
                "rate": body.base_rate, "zone": body.housekeeping_zone,
                "acc": body.accessibility, "elev": body.near_elevator,
                "status": body.status, "svc": body.service_status,
                "notes": body.notes, "active_from": body.active_from,
            },
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Room number {body.code} already exists"
        ) from exc

    if body.amenity_ids:
        _replace_room_amenities(
            db, room_id=room_id, property_id=property_id,
            amenity_ids=body.amenity_ids,
        )
    # Every room starts clean so housekeeping state is never NULL on the grid.
    db.execute(
        text(
            """
            INSERT INTO operations.room_condition
                (room_id, organization_id, property_id, cleanliness)
            VALUES (:rid, :org, :prop, 'clean')
            ON CONFLICT (room_id) DO NOTHING
            """
        ),
        {"rid": room_id, "org": caller.organization_id, "prop": property_id},
    )

    record_status_event(
        db, organization_id=caller.organization_id, property_id=property_id,
        room_id=room_id, status="available", source="user_update",
        changed_by=caller.user_id, remarks="Room created",
    )
    # A new room is one more the type can sell.
    sync_capacity(db, property_id=property_id, room_type_id=body.room_type_id)

    record_audit(
        db, action="room.create", entity_type="room", entity_id=str(room_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after=_room_snapshot(db, room_id),
    )
    return get_room(room_id, property_id, caller, db)


@rooms_router.put("/rooms/{room_id}", response_model=rs.RoomDetailOut)
def update_room(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    body: rs.RoomUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Update a room (screen 059, Edit mode). Optimistically locked."""
    assert_property_in_org(db, caller, property_id)
    _validate_choice(body.status, rs.ROOM_STATUSES, "status")
    _validate_choice(body.service_status, rs.SERVICE_STATUSES, "service_status")
    _validate_choice(body.accessibility, rs.ACCESSIBILITY, "accessibility")

    before = _room_snapshot(db, room_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Room not found")

    if body.room_type_id is not None:
        owns_type = db.execute(
            text(
                "SELECT 1 FROM property.room_types WHERE id = :rt AND property_id = :prop"
            ),
            {"rt": body.room_type_id, "prop": property_id},
        ).first()
        if owns_type is None:
            raise HTTPException(status_code=422, detail="Room type not in this property")

    place = (_resolve_floor(db, property_id=property_id, floor_id=body.floor_id)
             if body.floor_id else None)

    fields = {
        "room_type_id": body.room_type_id, "code": body.code,
        "building_id": place["building_id"] if place else None,
        "floor_id": place["floor_id"] if place else None,
        "building": place["building_name"] if place else body.building,
        "floor": place["floor_name"] if place else body.floor,
        "bed_setup": body.bed_setup, "view_type": body.view_type,
        "max_adults": body.max_adults, "max_children": body.max_children,
        "base_rate": body.base_rate, "housekeeping_zone": body.housekeeping_zone,
        "accessibility": body.accessibility, "near_elevator": body.near_elevator,
        "status": body.status, "service_status": body.service_status,
        "notes": body.notes,
    }
    set_parts = [f"{col} = :{col}" for col, val in fields.items() if val is not None]
    params = {col: val for col, val in fields.items() if val is not None}
    params.update({
        "id": room_id, "prop": property_id, "org": caller.organization_id,
        "version": body.version,
    })
    set_sql = ", ".join([*set_parts, "updated_at = now()", "version = version + 1"])

    try:
        result = db.execute(
            text(
                f"""
                UPDATE property.rooms SET {set_sql}
                WHERE id = :id AND property_id = :prop
                  AND organization_id = :org AND version = :version
                """
            ),
            params,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Room number already exists in this property"
        ) from exc

    if result.rowcount == 0:
        raise _conflict("Room")

    if body.amenity_ids is not None:
        _replace_room_amenities(
            db, room_id=room_id, property_id=property_id,
            amenity_ids=body.amenity_ids,
        )

    after = _room_snapshot(db, room_id)
    # Only a genuine state change belongs on the timeline; renaming a room or
    # editing its notes is an audit event, not a status event.
    if after and (
        after["status"] != before["status"]
        or after["service_status"] != before["service_status"]
    ):
        record_status_event(
            db, organization_id=caller.organization_id, property_id=property_id,
            room_id=room_id,
            status="inactive" if after["status"] != "active"
            else "out_of_order" if after["service_status"] == "out_of_service"
            else "maintenance" if after["service_status"] == "maintenance"
            else "available",
            source="user_update", changed_by=caller.user_id,
            remarks=body.reason or "Room details updated",
        )
    # An update can change whether the room is sellable, or move it to another
    # type entirely, so both the old type and the new one are resynced.
    for rt in {before["room_type_id"], body.room_type_id}:
        if rt:
            sync_capacity(db, property_id=property_id, room_type_id=rt)

    record_audit(
        db, action="room.update", entity_type="room", entity_id=str(room_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before=before,
        after=after, reason=body.reason,
    )
    return get_room(room_id, property_id, caller, db)


#: What makes a room undeletable, in the order a person would want to hear it.
#:
#: Five of these columns have no foreign key — reservation_units.assigned_room_id,
#: room_calendar_entries, stay_checkins, stay_checkouts and room_condition all
#: name a room without the database enforcing it. So Postgres will happily let a
#: delete orphan a guest's stay record, and these checks are the only thing
#: standing between a tidy-up and a bill that points at nothing.
_ROOM_HISTORY = (
    ("booking.reservation_units", "assigned_room_id",
     "has been assigned to {n} booking(s)"),
    ("booking.stay_checkins", "room_id", "has {n} check-in(s) recorded"),
    ("booking.stay_checkouts", "room_id", "has {n} check-out(s) recorded"),
    ("operations.housekeeping_tasks", "room_id",
     "has {n} housekeeping task(s)"),
)


def _room_history(db: Session, room_id: uuid.UUID) -> list[str]:
    """Everything that would be orphaned by deleting this room."""
    found = []
    for table, column, phrase in _ROOM_HISTORY:
        n = db.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :r"),
            {"r": room_id},
        ).scalar_one()
        if n:
            found.append(phrase.format(n=n))
    # A room move names two rooms, so it has to be asked about twice.
    n = db.execute(
        text("SELECT count(*) FROM booking.room_moves "
             "WHERE from_room_id = :r OR to_room_id = :r"),
        {"r": room_id},
    ).scalar_one()
    if n:
        found.append(f"is part of {n} room move(s)")
    # A calendar entry that belongs to a booking is a guest's room being held.
    # One that does not is a maintenance block, which goes with the room.
    n = db.execute(
        text("SELECT count(*) FROM booking.room_calendar_entries "
             "WHERE room_id = :r AND reservation_unit_id IS NOT NULL"),
        {"r": room_id},
    ).scalar_one()
    if n:
        found.append(f"was held for {n} booking(s)")
    return found


@rooms_router.post("/rooms/bulk-delete", response_model=rs.BulkDeleteOut)
def bulk_delete_rooms(
    property_id: uuid.UUID,
    body: rs.BulkDeleteIn,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Delete many rooms, keeping the ones that cannot go.

    **Partial on purpose.** Clearing out forty rooms seeded by mistake should
    not fail because three of them once had a guest — refusing the whole batch
    would leave the caller to find those three by hand. So every room is judged
    on its own, the clean ones go, and the rest come back with the reason.

    The same rule as the single delete decides each one: a room that any
    booking, stay or task has touched stays, because deleting it would leave
    those records pointing at nothing.
    """
    assert_property_in_org(db, caller, property_id)

    rooms = db.execute(
        text("SELECT id, code, status, room_type_id FROM property.rooms "
             "WHERE property_id = :prop AND id = ANY(:ids) ORDER BY code"),
        {"prop": property_id, "ids": body.room_ids},
    ).mappings().all()
    found = {r["id"] for r in rooms}
    missing = [i for i in body.room_ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"{len(missing)} of those rooms do not belong to this "
                   f"property.")

    results: list[rs.BulkDeleteRoomResult] = []
    doomed: list[uuid.UUID] = []
    # Rooms of the same type come off the count together, so the check has to
    # account for the ones already condemned in this batch -- otherwise each
    # room passes on its own and the batch as a whole still oversells.
    losing: dict[uuid.UUID, int] = {}
    for room in rooms:
        history = _room_history(db, room["id"])
        if history:
            results.append(rs.BulkDeleteRoomResult(
                id=room["id"], code=room["code"], deleted=False,
                reason=f"{', '.join(history)} — deactivate it instead.",
            ))
            continue

        rt = room["room_type_id"]
        if room["status"] == "active":
            losing[rt] = losing.get(rt, 0) + 1
            remaining = active_room_count(
                db, property_id=property_id, room_type_id=rt) - losing[rt]
            short = capacity_shortfall(
                db, property_id=property_id, room_type_id=rt,
                new_capacity=remaining)
            if short:
                losing[rt] -= 1
                days = ", ".join(d.strftime("%d %b") for d in short[:2])
                results.append(rs.BulkDeleteRoomResult(
                    id=room["id"], code=room["code"], deleted=False,
                    reason=f"its room type is fully booked on {days} — "
                           f"removing it would oversell those nights.",
                ))
                continue

        doomed.append(room["id"])
        results.append(rs.BulkDeleteRoomResult(
            id=room["id"], code=room["code"], deleted=True))

    if doomed:
        # Same order as the single delete: a block points at its calendar
        # entry, so the block goes first.
        for table in ("booking.room_blocks", "booking.room_calendar_entries",
                      "operations.room_condition"):
            db.execute(text(f"DELETE FROM {table} WHERE room_id = ANY(:ids)"),
                       {"ids": doomed})
        db.execute(
            text("DELETE FROM property.rooms "
                 "WHERE property_id = :prop AND id = ANY(:ids)"),
            {"prop": property_id, "ids": doomed},
        )
        for rt in losing:
            sync_capacity(db, property_id=property_id, room_type_id=rt)

        record_audit(
            db, action="room.bulk_delete", entity_type="room",
            entity_id=str(property_id),
            organization_id=caller.organization_id, property_id=property_id,
            actor_subject=caller.subject,
            before={"codes": [r.code for r in results if r.deleted]},
            reason=body.reason,
        )

    return rs.BulkDeleteOut(
        deleted=len(doomed), refused=len(results) - len(doomed),
        results=results,
    )


@rooms_router.delete("/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_room(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Delete a room that no guest, stay or task has ever touched.

    **Deleting is not the usual answer.** A room that has been sold is part of
    every folio, housekeeping task and status event that names it, and removing
    it would leave those pointing at nothing. Deactivating takes a room off
    sale and keeps all of that readable, which is what "we do not use this
    room any more" almost always means.

    So this refuses the moment the room carries history, and says exactly what
    the history is. It exists for the other case: a room typed in by mistake,
    or a seed that created more rooms than the property has.

    What goes with the room, because it is only ever about that room: its
    amenities, photos, status events (all by cascade), its maintenance blocks
    and their calendar entries, and its recorded condition.
    """
    assert_property_in_org(db, caller, property_id)
    room = db.execute(
        text("SELECT id, code, status, room_type_id FROM property.rooms "
             "WHERE id = :id AND property_id = :prop"),
        {"id": room_id, "prop": property_id},
    ).mappings().first()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    history = _room_history(db, room_id)
    if history:
        raise HTTPException(
            status_code=409,
            detail=f"Room {room['code']} {', '.join(history)}, so deleting it "
                   f"would leave those records pointing at nothing. "
                   f"Deactivate it instead — it comes off sale and the history "
                   f"stays readable.",
        )

    # Removing the room lowers what the type can sell. If a night is already
    # sold to the rooms that would remain, the delete is refused rather than
    # quietly leaving the type oversold.
    if room["status"] == "active":
        remaining = active_room_count(
            db, property_id=property_id, room_type_id=room["room_type_id"]) - 1
        short = capacity_shortfall(
            db, property_id=property_id, room_type_id=room["room_type_id"],
            new_capacity=remaining)
        if short:
            days = ", ".join(d.strftime("%d %b %Y") for d in short[:3])
            more = f" and {len(short) - 3} more" if len(short) > 3 else ""
            raise HTTPException(
                status_code=409,
                detail=f"Room {room['code']} cannot be removed: its room type "
                       f"is already fully booked on {days}{more}, so the "
                       f"property would be left holding more bookings than it "
                       f"has rooms.",
            )

    # A block and the calendar entry behind it are this room's own availability
    # and mean nothing without it. The entry is deleted after the block because
    # the block points at the entry.
    db.execute(
        text("DELETE FROM booking.room_blocks WHERE room_id = :r"),
        {"r": room_id},
    )
    db.execute(
        text("DELETE FROM booking.room_calendar_entries WHERE room_id = :r"),
        {"r": room_id},
    )
    db.execute(
        text("DELETE FROM operations.room_condition WHERE room_id = :r"),
        {"r": room_id},
    )
    # room_amenities, room_photos and room_status_events cascade.
    db.execute(
        text("DELETE FROM property.rooms WHERE id = :id AND property_id = :prop"),
        {"id": room_id, "prop": property_id},
    )

    # The type now has one room fewer, so its sellable capacity does too.
    sync_capacity(db, property_id=property_id,
                  room_type_id=room["room_type_id"])

    record_audit(
        db, action="room.delete", entity_type="room", entity_id=str(room_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        before={"code": room["code"], "status": room["status"]},
    )


@rooms_router.post("/rooms/{room_id}/service-status", response_model=rs.RoomDetailOut)
def set_room_service_status(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    body: rs.RoomServiceStatusIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Block / unblock a room — the Block button on screen 008.

    Taking a room out of service is refused while a guest is in house: the
    reservation data owns that room until checkout.
    """
    assert_property_in_org(db, caller, property_id)
    _validate_choice(body.service_status, rs.SERVICE_STATUSES, "service_status")

    before = _room_snapshot(db, room_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Room not found")

    if body.service_status != "in_service":
        in_house = db.execute(
            text(
                """
                SELECT 1
                FROM booking.reservation_units ru
                JOIN booking.stays s
                  ON s.reservation_unit_id = ru.id AND s.status = 'in_house'
                WHERE ru.assigned_room_id = :rid
                LIMIT 1
                """
            ),
            {"rid": room_id},
        ).first()
        if in_house is not None:
            raise HTTPException(
                status_code=409,
                detail="Room is occupied by an in-house stay and cannot be blocked",
            )

    result = db.execute(
        text(
            """
            UPDATE property.rooms
               SET service_status = :svc, updated_at = now(), version = version + 1
             WHERE id = :id AND property_id = :prop
               AND organization_id = :org AND version = :version
            """
        ),
        {
            "svc": body.service_status, "id": room_id, "prop": property_id,
            "org": caller.organization_id, "version": body.version,
        },
    )
    if result.rowcount == 0:
        raise _conflict("Room")

    record_status_event(
        db, organization_id=caller.organization_id, property_id=property_id,
        room_id=room_id,
        status="out_of_order" if body.service_status == "out_of_service"
        else "maintenance" if body.service_status == "maintenance" else "available",
        source="maintenance" if body.service_status != "in_service" else "user_update",
        changed_by=caller.user_id, remarks=body.reason,
    )
    record_audit(
        db,
        action="room.block" if body.service_status != "in_service" else "room.unblock",
        entity_type="room", entity_id=str(room_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before=before,
        after=_room_snapshot(db, room_id), reason=body.reason,
    )
    return get_room(room_id, property_id, caller, db)

# --------------------------------------------------------------------------
# Screen 060 — Room Type Management
# --------------------------------------------------------------------------
_ROOM_TYPE_SELECT = """
    SELECT rt.id, rt.code, rt.name, rt.description, rt.max_adults,
           rt.max_children, rt.max_occupancy, rt.base_rate, rt.bed_setup,
           rt.size_sqft, rt.child_policy, rt.extra_bed_available,
           rt.extra_bed_charge, rt.room_view, rt.default_rate_plan,
           rt.status, rt.version, rt.created_at, rt.updated_at,
           cu.display_name AS created_by_name,
           uu.display_name AS updated_by_name,
           (SELECT count(*) FROM property.rooms r
             WHERE r.room_type_id = rt.id) AS room_count,
           (SELECT count(*) FROM property.rooms r
             WHERE r.room_type_id = rt.id AND r.status = 'active') AS active_room_count,
           (SELECT p.url FROM property.room_type_photos p
             WHERE p.room_type_id = rt.id AND p.is_primary LIMIT 1) AS primary_photo_url,
           (SELECT p.storage_key FROM property.room_type_photos p
             WHERE p.room_type_id = rt.id AND p.is_primary LIMIT 1) AS primary_storage_key
    FROM property.room_types rt
    LEFT JOIN iam.users cu ON cu.id = rt.created_by
    LEFT JOIN iam.users uu ON uu.id = rt.updated_by
"""

_SORTS = {
    "name_asc": "rt.name ASC",
    "name_desc": "rt.name DESC",
    "rate_asc": "rt.base_rate ASC NULLS LAST",
    "rate_desc": "rt.base_rate DESC NULLS LAST",
    "rooms_desc": "room_count DESC",
}


def _room_type_amenity_ids(
    db: Session, room_type_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Amenity ids per room type, fetched in one query."""
    if not room_type_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT room_type_id, amenity_id
            FROM property.room_type_amenities
            WHERE room_type_id = ANY(:ids)
            """
        ),
        {"ids": room_type_ids},
    ).all()
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in rows:
        out.setdefault(row.room_type_id, []).append(row.amenity_id)
    return out


def _replace_room_type_amenities(
    db: Session, *, room_type_id: uuid.UUID, property_id: uuid.UUID,
    amenity_ids: list[uuid.UUID],
) -> None:
    db.execute(
        text("DELETE FROM property.room_type_amenities WHERE room_type_id = :id"),
        {"id": room_type_id},
    )
    for amenity_id in dict.fromkeys(amenity_ids):
        db.execute(
            text(
                """
                INSERT INTO property.room_type_amenities
                    (room_type_id, amenity_id, property_id)
                VALUES (:rt, :a, :prop)
                """
            ),
            {"rt": room_type_id, "a": amenity_id, "prop": property_id},
        )


def _room_type_rows(
    db: Session, property_id: uuid.UUID, where: list[str] | None = None,
    params: dict[str, Any] | None = None, order: str = "rt.name ASC",
) -> list[rs.RoomTypeDetailOut]:
    clauses = ["rt.property_id = :prop", *(where or [])]
    p: dict[str, Any] = {"prop": property_id, **(params or {})}
    rows = db.execute(
        text(f"{_ROOM_TYPE_SELECT} WHERE {' AND '.join(clauses)} ORDER BY {order}"),
        p,
    ).mappings().all()
    amenities = _room_type_amenity_ids(db, [r["id"] for r in rows])
    out: list[rs.RoomTypeDetailOut] = []
    for r in rows:
        row = dict(r)
        # Presign the thumbnail; uploaded photos have a key, not a URL.
        row["primary_photo_url"] = photo_url(
            row.pop("primary_photo_url"), row.pop("primary_storage_key")
        )
        out.append(rs.RoomTypeDetailOut(**row, amenity_ids=amenities.get(r["id"], [])))
    return out


def _list_room_types(db: Session, property_id: uuid.UUID) -> list[rs.RoomTypeDetailOut]:
    """Plain listing, also used for the Add/Edit Room dropdowns."""
    return _room_type_rows(db, property_id)


@rooms_router.get("/room-types/stats", response_model=rs.RoomTypeStatsOut)
def room_type_stats(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The four KPI cards on screen 060."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT
              count(*)                                        AS room_types,
              count(*) FILTER (WHERE rt.status = 'active')    AS active_types,
              count(*) FILTER (WHERE rt.status <> 'active')   AS inactive_types,
              COALESCE(sum(rc.total), 0)                      AS total_rooms,
              COALESCE(sum(rc.active), 0)                     AS active_rooms,
              COALESCE(sum(rt.max_occupancy * rc.total), 0)   AS max_guest_capacity,
              round(avg(rt.base_rate) FILTER (WHERE rt.base_rate IS NOT NULL), 0)
                                                              AS average_base_rate
            FROM property.room_types rt
            LEFT JOIN LATERAL (
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE r.status = 'active') AS active
                FROM property.rooms r WHERE r.room_type_id = rt.id
            ) rc ON TRUE
            WHERE rt.property_id = :prop
            """
        ),
        {"prop": property_id},
    ).mappings().one()
    return rs.RoomTypeStatsOut(
        room_types=row["room_types"],
        active_types=row["active_types"],
        inactive_types=row["inactive_types"],
        total_rooms=row["total_rooms"],
        active_rooms=row["active_rooms"],
        inactive_rooms=row["total_rooms"] - row["active_rooms"],
        max_guest_capacity=row["max_guest_capacity"],
        average_base_rate=row["average_base_rate"],
    )


@rooms_router.get("/room-types/views", response_model=list[str])
def room_type_views(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Distinct room views, for screen 060's "Room View" filter."""
    assert_property_in_org(db, caller, property_id)
    return [
        r[0]
        for r in db.execute(
            text(
                "SELECT DISTINCT room_view FROM property.room_types "
                "WHERE property_id = :prop AND room_view IS NOT NULL ORDER BY 1"
            ),
            {"prop": property_id},
        ).all()
    ]


@rooms_router.get("/room-types/manage", response_model=list[rs.RoomTypeDetailOut])
def list_room_types_managed(
    property_id: uuid.UUID,
    search: str | None = None,
    status: str | None = None,
    room_view: str | None = None,
    sort: str = Query(default="name_asc"),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    where: list[str] = []
    params: dict[str, Any] = {}
    if search:
        where.append("(rt.name ILIKE :q OR rt.code ILIKE :q)")
        params["q"] = f"%{search}%"
    if status:
        _validate_choice(status, ("active", "inactive"), "status")
        where.append("rt.status = :status")
        params["status"] = status
    if room_view:
        where.append("rt.room_view = :view")
        params["view"] = room_view
    return _room_type_rows(
        db, property_id, where, params, _SORTS.get(sort, _SORTS["name_asc"])
    )


@rooms_router.get(
    "/room-types/manage/{room_type_id}", response_model=rs.RoomTypeDetailOut
)
def get_room_type_managed(
    room_type_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    rows = _room_type_rows(db, property_id, ["rt.id = :id"], {"id": room_type_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Room type not found")
    detail = rows[0]
    # Uploaded photos are stored as object keys; presign them for the browser.
    detail.photos = _photos_out(
        _photo_rows(db, "room_type_photos", "room_type_id", room_type_id)
    )
    detail.primary_photo_url = detail.photos[0].url if detail.photos else None
    return detail


def _insert_room_type(
    db: Session, *, caller: Caller, property_id: uuid.UUID, body: rs.RoomTypeIn
) -> uuid.UUID:
    rt_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.room_types
                    (id, organization_id, property_id, code, name, description,
                     max_adults, max_children, max_occupancy, base_rate,
                     bed_setup, size_sqft, child_policy, extra_bed_available,
                     extra_bed_charge, room_view, default_rate_plan, status,
                     created_by, updated_by)
                VALUES (:id, :org, :prop, :code, :name, :descr, :ad, :ch, :occ,
                        :rate, :bed, :size, :cpol, :xbed, :xcharge, :view,
                        :plan, :status, :actor, :actor)
                """
            ),
            {
                "id": rt_id, "org": caller.organization_id, "prop": property_id,
                "code": body.code, "name": body.name, "descr": body.description,
                "ad": body.max_adults, "ch": body.max_children,
                "occ": body.max_occupancy, "rate": body.base_rate,
                "bed": body.bed_setup, "size": body.size_sqft,
                "cpol": body.child_policy, "xbed": body.extra_bed_available,
                "xcharge": body.extra_bed_charge, "view": body.room_view,
                "plan": body.default_rate_plan, "status": body.status,
                "actor": caller.user_id,
            },
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Room type code {body.code} already exists"
        ) from exc
    if body.amenity_ids:
        _replace_room_type_amenities(
            db, room_type_id=rt_id, property_id=property_id,
            amenity_ids=body.amenity_ids,
        )
    return rt_id


@rooms_router.post(
    "/room-types/manage",
    response_model=rs.RoomTypeDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def create_room_type_managed(
    body: rs.RoomTypeIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate_choice(body.status, ("active", "inactive"), "status")
    if body.max_occupancy < body.max_adults:
        raise HTTPException(
            status_code=422, detail="max_occupancy cannot be below max_adults"
        )
    rt_id = _insert_room_type(db, caller=caller, property_id=property_id, body=body)
    record_audit(
        db, action="room_type.create", entity_type="room_type", entity_id=str(rt_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={"code": body.code, "name": body.name, "status": body.status},
    )
    return get_room_type_managed(rt_id, property_id, caller, db)


@rooms_router.put(
    "/room-types/manage/{room_type_id}", response_model=rs.RoomTypeDetailOut
)
def update_room_type_managed(
    room_type_id: uuid.UUID,
    property_id: uuid.UUID,
    body: rs.RoomTypeUpdate,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate_choice(body.status, ("active", "inactive"), "status")

    before = db.execute(
        text(
            """
            SELECT code, name, max_adults, max_children, max_occupancy,
                   base_rate, child_policy, extra_bed_available, room_view,
                   status, version
            FROM property.room_types WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": room_type_id, "prop": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Room type not found")

    fields = {
        "name": body.name, "description": body.description,
        "max_adults": body.max_adults, "max_children": body.max_children,
        "max_occupancy": body.max_occupancy, "base_rate": body.base_rate,
        "bed_setup": body.bed_setup, "size_sqft": body.size_sqft,
        "child_policy": body.child_policy,
        "extra_bed_available": body.extra_bed_available,
        "extra_bed_charge": body.extra_bed_charge,
        "room_view": body.room_view,
        "default_rate_plan": body.default_rate_plan,
        "status": body.status,
    }
    set_parts = [f"{c} = :{c}" for c, v in fields.items() if v is not None]
    params = {c: v for c, v in fields.items() if v is not None}
    params.update({
        "id": room_type_id, "prop": property_id, "version": body.version,
        "actor": caller.user_id,
    })
    set_sql = ", ".join(
        [*set_parts, "updated_at = now()", "updated_by = :actor", "version = version + 1"]
    )
    result = db.execute(
        text(
            f"""
            UPDATE property.room_types SET {set_sql}
            WHERE id = :id AND property_id = :prop AND version = :version
            """
        ),
        params,
    )
    if result.rowcount == 0:
        raise _conflict("Room type")

    if body.amenity_ids is not None:
        _replace_room_type_amenities(
            db, room_type_id=room_type_id, property_id=property_id,
            amenity_ids=body.amenity_ids,
        )

    record_audit(
        db, action="room_type.update", entity_type="room_type",
        entity_id=str(room_type_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={k: _jsonable(v) for k, v in before.items()},
        after={k: _jsonable(v) for k, v in params.items()
               if k not in ("id", "prop", "version", "actor")},
        reason=body.reason,
    )
    return get_room_type_managed(room_type_id, property_id, caller, db)


def _room_type_history(
    db: Session, *, room_type_id: uuid.UUID, property_id: uuid.UUID
) -> list[str]:
    """What still depends on this room type, in plain words.

    Each entry is a clause the refusal message joins together, so the operator
    is told everything that is in the way at once rather than clearing one
    blocker only to hit the next.
    """
    counts = db.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM property.rooms
                WHERE room_type_id = :rt)                        AS rooms,
              (SELECT count(*) FROM booking.reservation_units
                WHERE room_type_id = :rt)                        AS units,
              (SELECT count(*) FROM property.rate_plan_room_types
                WHERE room_type_id = :rt)                        AS plans,
              (SELECT count(*) FROM property.package_room_types
                WHERE room_type_id = :rt)                        AS packages,
              (SELECT count(*) FROM property.rate_rule_room_types
                WHERE room_type_id = :rt)                        AS rules,
              (SELECT count(*) FROM engagement.enquiries
                WHERE room_type_id = :rt)                        AS enquiries
            """
        ),
        {"rt": room_type_id},
    ).mappings().first()

    def phrase(n: int, one: str, many: str) -> str | None:
        return None if n == 0 else f"{n} {one if n == 1 else many}"

    parts = [
        phrase(counts["rooms"], "room", "rooms"),
        phrase(counts["units"], "booking", "bookings"),
        phrase(counts["plans"], "rate plan", "rate plans"),
        phrase(counts["packages"], "package", "packages"),
        phrase(counts["rules"], "rate rule", "rate rules"),
        phrase(counts["enquiries"], "enquiry", "enquiries"),
    ]
    return [x for x in parts if x]


@rooms_router.delete(
    "/room-types/manage/{room_type_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_room_type_managed(
    room_type_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Delete a room type nothing has ever used.

    **Deleting is not the usual answer.** A type that has been sold is named by
    every booking, folio line and rate that referred to it, and removing it
    would leave those pointing at nothing. Deactivating takes it off sale and
    keeps all of that readable, which is what "we do not offer this any more"
    almost always means.

    So this refuses the moment anything depends on the type, and says what.
    It exists for the other case: a type created by mistake during setup, which
    until now could only be deactivated -- and, because the onboarding gate
    counted it, went on demanding a rate forever.

    What goes with the type, because it is only ever about that type: its
    amenity links, its photos (files included), its inventory counters and its
    rate-calendar rows.
    """
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text("SELECT id, name, code FROM property.room_types "
             "WHERE id = :id AND property_id = :prop"),
        {"id": room_type_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Room type not found")

    blockers = _room_type_history(
        db, room_type_id=room_type_id, property_id=property_id)
    if blockers:
        raise HTTPException(
            status_code=409,
            detail=f"{row['name']} still has {', '.join(blockers)}. Remove or "
                   f"move those first, or deactivate the type instead — it "
                   f"comes off sale and the history stays readable.",
        )

    # The photo files go too; the rows alone would leave objects nobody can
    # reach, paid for and never read again.
    for photo in db.execute(
        text("SELECT storage_key FROM property.room_type_photos "
             "WHERE room_type_id = :rt"),
        {"rt": room_type_id},
    ).mappings().all():
        if photo["storage_key"]:
            delete_object(_STORE, photo["storage_key"])

    for sql in (
        "DELETE FROM property.room_type_photos WHERE room_type_id = :rt",
        "DELETE FROM property.room_type_amenities WHERE room_type_id = :rt",
        "DELETE FROM booking.room_type_inventory_days WHERE room_type_id = :rt",
        "DELETE FROM property.rate_calendar_days WHERE room_type_id = :rt",
    ):
        db.execute(text(sql), {"rt": room_type_id})

    db.execute(
        text("DELETE FROM property.room_types WHERE id = :id AND property_id = :prop"),
        {"id": room_type_id, "prop": property_id},
    )

    record_audit(
        db, action="room_type.delete", entity_type="room_type",
        entity_id=str(room_type_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={"name": row["name"], "code": row["code"]},
    )
    return None


@rooms_router.post(
    "/room-types/manage/{room_type_id}/duplicate",
    response_model=rs.RoomTypeDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_room_type(
    room_type_id: uuid.UUID,
    property_id: uuid.UUID,
    body: rs.RoomTypeDuplicateIn,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Screen 060 'Duplicate': copy the type and its amenity set, never its rooms."""
    assert_property_in_org(db, caller, property_id)
    src = db.execute(
        text(
            """
            SELECT code, name, description, max_adults, max_children,
                   max_occupancy, base_rate, bed_setup, size_sqft, child_policy,
                   extra_bed_available, extra_bed_charge, room_view,
                   default_rate_plan
            FROM property.room_types WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": room_type_id, "prop": property_id},
    ).mappings().first()
    if src is None:
        raise HTTPException(status_code=404, detail="Room type not found")

    # A copy must not collide with the original's unique (property, code).
    new_code = body.code or f"{src['code']}-COPY"
    new_name = body.name or f"{src['name']} (Copy)"
    exists = db.execute(
        text(
            "SELECT 1 FROM property.room_types WHERE property_id = :prop AND code = :code"
        ),
        {"prop": property_id, "code": new_code},
    ).first()
    if exists is not None:
        raise HTTPException(
            status_code=409, detail=f"Room type code {new_code} already exists"
        )

    amenity_ids = _room_type_amenity_ids(db, [room_type_id]).get(room_type_id, [])
    clone = rs.RoomTypeIn(
        **{**dict(src), "code": new_code, "name": new_name, "status": "inactive"},
        amenity_ids=amenity_ids,
    )
    new_id = _insert_room_type(db, caller=caller, property_id=property_id, body=clone)
    record_audit(
        db, action="room_type.duplicate", entity_type="room_type",
        entity_id=str(new_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={"source_room_type_id": str(room_type_id)},
        after={"code": new_code, "name": new_name, "status": "inactive"},
    )
    return get_room_type_managed(new_id, property_id, caller, db)


@rooms_router.post(
    "/room-types/manage/{room_type_id}/status", response_model=rs.RoomTypeDetailOut
)
def set_room_type_status(
    room_type_id: uuid.UUID,
    property_id: uuid.UUID,
    body: rs.StatusChangeIn,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Screen 060 'Deactivate'. Refused while active rooms still use the type."""
    assert_property_in_org(db, caller, property_id)
    _validate_choice(body.status, ("active", "inactive"), "status")

    if body.status == "inactive":
        in_use = db.execute(
            text(
                """
                SELECT count(*) FROM property.rooms
                WHERE room_type_id = :id AND status = 'active'
                """
            ),
            {"id": room_type_id},
        ).scalar_one()
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=f"{in_use} active room(s) still use this type; "
                       "reassign or deactivate them first",
            )

    result = db.execute(
        text(
            """
            UPDATE property.room_types
               SET status = :status, updated_at = now(), updated_by = :actor,
                   version = version + 1
             WHERE id = :id AND property_id = :prop AND version = :version
            """
        ),
        {
            "status": body.status, "id": room_type_id, "prop": property_id,
            "version": body.version, "actor": caller.user_id,
        },
    )
    if result.rowcount == 0:
        raise _conflict("Room type")

    record_audit(
        db, action=f"room_type.{body.status}", entity_type="room_type",
        entity_id=str(room_type_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"status": body.status}, reason=body.reason,
    )
    return get_room_type_managed(room_type_id, property_id, caller, db)


# --------------------------------------------------------------------------
# Screen 062 — Amenities Management
# --------------------------------------------------------------------------
_AMENITY_SELECT = """
    SELECT a.id, a.code, a.name, a.category, a.icon, a.is_chargeable,
           a.guest_visible, a.description, a.status, a.version,
           a.created_at, a.updated_at,
           cu.display_name AS created_by_name,
           uu.display_name AS updated_by_name,
           (SELECT count(*) FROM property.room_amenities ra
             WHERE ra.amenity_id = a.id) AS room_count
    FROM property.amenities a
    LEFT JOIN iam.users cu ON cu.id = a.created_by
    LEFT JOIN iam.users uu ON uu.id = a.updated_by
"""


def _amenity_room_types(
    db: Session, amenity_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[rs.NamedRef]]:
    if not amenity_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT rta.amenity_id, rt.id, rt.name
            FROM property.room_type_amenities rta
            JOIN property.room_types rt ON rt.id = rta.room_type_id
            WHERE rta.amenity_id = ANY(:ids)
            ORDER BY rt.name
            """
        ),
        {"ids": amenity_ids},
    ).all()
    out: dict[uuid.UUID, list[rs.NamedRef]] = {}
    for row in rows:
        out.setdefault(row.amenity_id, []).append(
            rs.NamedRef(id=row.id, name=row.name)
        )
    return out


def _replace_amenity_room_types(
    db: Session, *, amenity_id: uuid.UUID, property_id: uuid.UUID,
    room_type_ids: list[uuid.UUID],
) -> None:
    db.execute(
        text("DELETE FROM property.room_type_amenities WHERE amenity_id = :id"),
        {"id": amenity_id},
    )
    for rt_id in dict.fromkeys(room_type_ids):
        db.execute(
            text(
                """
                INSERT INTO property.room_type_amenities
                    (room_type_id, amenity_id, property_id)
                VALUES (:rt, :a, :prop)
                """
            ),
            {"rt": rt_id, "a": amenity_id, "prop": property_id},
        )


def _amenity_rows(
    db: Session, where: list[str], params: dict[str, Any]
) -> list[rs.AmenityOut]:
    rows = db.execute(
        text(f"{_AMENITY_SELECT} WHERE {' AND '.join(where)} ORDER BY a.category, a.name"),
        params,
    ).mappings().all()
    rts = _amenity_room_types(db, [r["id"] for r in rows])
    return [
        rs.AmenityOut(
            **r,
            category_label=rs.AMENITY_CATEGORY_LABELS.get(r["category"], r["category"]),
            room_types=rts.get(r["id"], []),
        )
        for r in rows
    ]


@rooms_router.get("/amenities/stats", response_model=rs.AmenityStatsOut)
def amenity_stats(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """KPI cards plus the per-category counts for screen 062's tabs."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE guest_visible)          AS guest_visible,
                   count(*) FILTER (WHERE status = 'active')      AS active,
                   count(*) FILTER (WHERE status <> 'active')     AS inactive
            FROM property.amenities WHERE property_id = :prop
            """
        ),
        {"prop": property_id},
    ).mappings().one()
    by_cat = {
        r.category: r.n
        for r in db.execute(
            text(
                "SELECT category, count(*) AS n FROM property.amenities "
                "WHERE property_id = :prop GROUP BY category"
            ),
            {"prop": property_id},
        ).all()
    }
    return rs.AmenityStatsOut(**row, by_category=by_cat)


@rooms_router.get("/amenities", response_model=rs.AmenityListOut)
def list_amenities(
    property_id: uuid.UUID,
    search: str | None = None,
    category: str | None = None,
    status: str | None = None,
    guest_visible: bool | None = None,
    limit: int = Query(default=10, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    where = ["a.property_id = :prop"]
    params: dict[str, Any] = {"prop": property_id}
    if search:
        where.append("(a.name ILIKE :q OR a.code ILIKE :q)")
        params["q"] = f"%{search}%"
    if category:
        _validate_choice(category, rs.AMENITY_CATEGORIES, "category")
        where.append("a.category = :cat")
        params["cat"] = category
    if status:
        _validate_choice(status, ("active", "inactive"), "status")
        where.append("a.status = :status")
        params["status"] = status
    if guest_visible is not None:
        where.append("a.guest_visible = :gv")
        params["gv"] = guest_visible

    total = db.execute(
        text(f"SELECT count(*) FROM property.amenities a WHERE {' AND '.join(where)}"),
        params,
    ).scalar_one()
    rows = db.execute(
        text(
            f"{_AMENITY_SELECT} WHERE {' AND '.join(where)} "
            "ORDER BY a.category, a.name LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    rts = _amenity_room_types(db, [r["id"] for r in rows])
    items = [
        rs.AmenityOut(
            **r,
            category_label=rs.AMENITY_CATEGORY_LABELS.get(r["category"], r["category"]),
            room_types=rts.get(r["id"], []),
        )
        for r in rows
    ]
    return rs.AmenityListOut(items=items, total=total)


def _one_amenity(
    db: Session, amenity_id: uuid.UUID, property_id: uuid.UUID
) -> rs.AmenityOut:
    rows = _amenity_rows(
        db, ["a.id = :id", "a.property_id = :prop"],
        {"id": amenity_id, "prop": property_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Amenity not found")
    return rows[0]


@rooms_router.post(
    "/amenities", response_model=rs.AmenityOut, status_code=status.HTTP_201_CREATED
)
def create_amenity(
    body: rs.AmenityIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate_choice(body.category, rs.AMENITY_CATEGORIES, "category")
    _validate_choice(body.status, ("active", "inactive"), "status")

    amenity_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.amenities
                    (id, organization_id, property_id, code, name, category,
                     icon, is_chargeable, guest_visible, description, status,
                     created_by, updated_by)
                VALUES (:id, :org, :prop, :code, :name, :cat, :icon, :chg,
                        :gv, :descr, :status, :actor, :actor)
                """
            ),
            {
                "id": amenity_id, "org": caller.organization_id,
                "prop": property_id, "code": body.code, "name": body.name,
                "cat": body.category, "icon": body.icon,
                "chg": body.is_chargeable, "gv": body.guest_visible,
                "descr": body.description, "status": body.status,
                "actor": caller.user_id,
            },
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Amenity code {body.code} already exists"
        ) from exc

    if body.room_type_ids:
        _replace_amenity_room_types(
            db, amenity_id=amenity_id, property_id=property_id,
            room_type_ids=body.room_type_ids,
        )
    record_audit(
        db, action="amenity.create", entity_type="amenity",
        entity_id=str(amenity_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"code": body.code, "name": body.name, "category": body.category,
               "guest_visible": body.guest_visible},
    )
    return _one_amenity(db, amenity_id, property_id)


@rooms_router.put("/amenities/{amenity_id}", response_model=rs.AmenityOut)
def update_amenity(
    amenity_id: uuid.UUID,
    property_id: uuid.UUID,
    body: rs.AmenityUpdate,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate_choice(body.category, rs.AMENITY_CATEGORIES, "category")
    _validate_choice(body.status, ("active", "inactive"), "status")

    before = db.execute(
        text(
            """
            SELECT code, name, category, is_chargeable, guest_visible,
                   status, version
            FROM property.amenities WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": amenity_id, "prop": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Amenity not found")

    fields = {
        "name": body.name, "category": body.category, "icon": body.icon,
        "is_chargeable": body.is_chargeable, "guest_visible": body.guest_visible,
        "description": body.description, "status": body.status,
    }
    set_parts = [f"{c} = :{c}" for c, v in fields.items() if v is not None]
    params = {c: v for c, v in fields.items() if v is not None}
    params.update({
        "id": amenity_id, "prop": property_id, "version": body.version,
        "actor": caller.user_id,
    })
    set_sql = ", ".join(
        [*set_parts, "updated_at = now()", "updated_by = :actor", "version = version + 1"]
    )
    result = db.execute(
        text(
            f"""
            UPDATE property.amenities SET {set_sql}
            WHERE id = :id AND property_id = :prop AND version = :version
            """
        ),
        params,
    )
    if result.rowcount == 0:
        raise _conflict("Amenity")

    if body.room_type_ids is not None:
        _replace_amenity_room_types(
            db, amenity_id=amenity_id, property_id=property_id,
            room_type_ids=body.room_type_ids,
        )
    record_audit(
        db, action="amenity.update", entity_type="amenity",
        entity_id=str(amenity_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={k: _jsonable(v) for k, v in before.items()},
        after={k: _jsonable(v) for k, v in params.items()
               if k not in ("id", "prop", "version", "actor")},
    )
    return _one_amenity(db, amenity_id, property_id)


@rooms_router.post("/amenities/{amenity_id}/merge", response_model=rs.AmenityOut)
def merge_amenity(
    amenity_id: uuid.UUID,
    property_id: uuid.UUID,
    body: rs.AmenityMergeIn,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Screen 062 'Merge Duplicate'.

    Moves every room and room-type assignment from the duplicate onto the
    target, then retires the duplicate. Assignments the target already has are
    skipped rather than duplicated, and the whole move is one transaction.
    """
    assert_property_in_org(db, caller, property_id)
    if amenity_id == body.target_amenity_id:
        raise HTTPException(status_code=422, detail="Cannot merge an amenity into itself")

    both = db.execute(
        text(
            """
            SELECT id, code, name FROM property.amenities
            WHERE property_id = :prop AND id = ANY(:ids)
            """
        ),
        {"prop": property_id, "ids": [amenity_id, body.target_amenity_id]},
    ).mappings().all()
    if len(both) != 2:
        raise HTTPException(status_code=404, detail="Amenity or merge target not found")

    moved_rooms = db.execute(
        text(
            """
            INSERT INTO property.room_amenities (room_id, amenity_id, property_id)
            SELECT ra.room_id, :target, ra.property_id
            FROM property.room_amenities ra
            WHERE ra.amenity_id = :dup
            ON CONFLICT DO NOTHING
            """
        ),
        {"dup": amenity_id, "target": body.target_amenity_id},
    ).rowcount
    moved_types = db.execute(
        text(
            """
            INSERT INTO property.room_type_amenities
                (room_type_id, amenity_id, property_id)
            SELECT rta.room_type_id, :target, rta.property_id
            FROM property.room_type_amenities rta
            WHERE rta.amenity_id = :dup
            ON CONFLICT DO NOTHING
            """
        ),
        {"dup": amenity_id, "target": body.target_amenity_id},
    ).rowcount
    db.execute(
        text("DELETE FROM property.room_amenities WHERE amenity_id = :dup"),
        {"dup": amenity_id},
    )
    db.execute(
        text("DELETE FROM property.room_type_amenities WHERE amenity_id = :dup"),
        {"dup": amenity_id},
    )
    db.execute(
        text(
            """
            UPDATE property.amenities
               SET status = 'inactive', updated_at = now(), updated_by = :actor,
                   version = version + 1
             WHERE id = :dup AND property_id = :prop
            """
        ),
        {"dup": amenity_id, "prop": property_id, "actor": caller.user_id},
    )
    record_audit(
        db, action="amenity.merge", entity_type="amenity", entity_id=str(amenity_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        before={"duplicate_id": str(amenity_id)},
        after={
            "target_id": str(body.target_amenity_id),
            "rooms_moved": moved_rooms,
            "room_types_moved": moved_types,
        },
        reason=body.reason,
    )
    return _one_amenity(db, body.target_amenity_id, property_id)

@rooms_router.get(
    "/rooms/{room_id}/assignable", response_model=list[rs.AssignableUnitOut]
)
def assignable_units(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Reservation units this room could be assigned to (screen 008 'Assign').

    Only units of the room's own type, still unassigned, not cancelled, and not
    already departed. The actual assignment goes through the existing
    ``/reservation-units/{id}/assign`` endpoint, where the GiST exclusion
    constraint is what really guarantees no double-booking.
    """
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT ru.id AS reservation_unit_id, ru.reservation_id,
                   res.number AS reservation_number, g.full_name AS guest_name,
                   ru.arrival_date, ru.departure_date, ru.adults, ru.children,
                   ru.status
            FROM booking.reservation_units ru
            JOIN property.rooms r
              ON r.id = :room AND r.property_id = ru.property_id
             AND r.room_type_id = ru.room_type_id
            LEFT JOIN booking.reservations res ON res.id = ru.reservation_id
            LEFT JOIN engagement.guests g ON g.id = res.primary_guest_id
            WHERE ru.property_id = :prop
              AND ru.organization_id = :org
              AND ru.assigned_room_id IS NULL
              AND ru.status NOT IN ('cancelled', 'checked_out', 'no_show')
              AND ru.departure_date >= CURRENT_DATE
            ORDER BY ru.arrival_date
            LIMIT 50
            """
        ),
        {"room": room_id, "prop": property_id, "org": caller.organization_id},
    ).mappings().all()
    return [rs.AssignableUnitOut(**r) for r in rows]


# --------------------------------------------------------------------------
# Photos (screens 059 / 060) — stored in MinIO, presigned on read
# --------------------------------------------------------------------------
_STORE = ObjectStoreConfig(
    endpoint=settings.minio_endpoint,
    public_endpoint=settings.minio_public_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    bucket=settings.minio_bucket,
    secure=settings.minio_secure,
    url_ttl_seconds=settings.minio_url_ttl_seconds,
)


def photo_url(url: str | None, storage_key: str | None,
              thumb_key: str | None = None) -> str | None:
    """Resolve a stored photo to something the browser can load.

    An uploaded photo has only a key, so it is presigned here. A photo hosted
    elsewhere keeps its absolute URL.

    ``thumb_key`` asks for the small version, and is the whole point of the
    thumbnail: a list of room types shows each picture a hundred pixels wide,
    and sending the original to do that is megabytes spent on pixels nobody
    sees. Passing a key that is ``None`` -- an old photo, or one that could not
    be resized -- quietly falls back to the original, so a caller never has to
    ask whether a thumbnail exists.
    """
    chosen = thumb_key or storage_key
    if chosen:
        try:
            return presigned_url(_STORE, chosen)
        except Exception:  # noqa: BLE001 - a broken store must not break the page
            return None
    return url


def _photo_rows(db: Session, table: str, owner_col: str, owner_id: uuid.UUID):
    return db.execute(
        text(
            f"""
            SELECT id, url, storage_key, caption, is_primary, sort_order
            FROM property.{table}
            WHERE {owner_col} = :id
            ORDER BY is_primary DESC, sort_order, created_at
            """
        ),
        {"id": owner_id},
    ).mappings().all()


def _photos_out(rows) -> list[rs.RoomPhotoOut]:
    out: list[rs.RoomPhotoOut] = []
    for r in rows:
        resolved = photo_url(r["url"], r["storage_key"])
        if resolved is None:
            continue
        out.append(
            rs.RoomPhotoOut(
                id=r["id"], url=resolved, caption=r["caption"],
                is_primary=r["is_primary"], sort_order=r["sort_order"],
            )
        )
    return out


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422, detail="Only JPG, PNG or WebP images are accepted"
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is larger than 5 MB")
    return data, content_type


def _insert_photo(
    db: Session, *, table: str, owner_col: str, owner_id: uuid.UUID,
    property_id: uuid.UUID, key: str, content_type: str, size: int,
    caller: Caller, caption: str | None, make_primary: bool = False,
    thumb_key: str | None = None,
) -> uuid.UUID:
    """Insert the row.

    The new photo becomes primary when it is the owner's first, or when the
    caller asked for it ("Change Photo" replaces the hero, "Add More" does not).
    """
    if make_primary:
        # The partial unique index permits only one primary row per owner.
        db.execute(
            text(
                f"UPDATE property.{table} SET is_primary = false "
                f"WHERE {owner_col} = :id AND is_primary"
            ),
            {"id": owner_id},
        )
    has_primary = None if make_primary else db.execute(
        text(f"SELECT 1 FROM property.{table} WHERE {owner_col} = :id AND is_primary"),
        {"id": owner_id},
    ).first()
    next_order = db.execute(
        text(
            f"SELECT COALESCE(max(sort_order), -1) + 1 FROM property.{table} "
            f"WHERE {owner_col} = :id"
        ),
        {"id": owner_id},
    ).scalar_one()
    photo_id = uuid.uuid4()
    db.execute(
        text(
            f"""
            INSERT INTO property.{table}
                (id, {owner_col}, property_id, storage_key, content_type,
                 size_bytes, caption, is_primary, sort_order, uploaded_by,
                 thumb_key)
            VALUES (:id, :owner, :prop, :key, :ct, :size, :caption, :primary,
                    :order, :actor, :thumb)
            """
        ),
        {
            "id": photo_id, "owner": owner_id, "prop": property_id, "key": key,
            "ct": content_type, "size": size, "caption": caption,
            "primary": has_primary is None, "order": next_order,
            "actor": caller.user_id, "thumb": thumb_key,
        },
    )
    return photo_id


@rooms_router.post(
    "/room-types/manage/{room_type_id}/photos",
    response_model=rs.RoomTypeDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_room_type_photo(
    room_type_id: uuid.UUID,
    property_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
    make_primary: bool = Form(default=False),
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Screen 060 'Add More' / 'Change Photo'."""
    assert_property_in_org(db, caller, property_id)
    owns = db.execute(
        text("SELECT 1 FROM property.room_types WHERE id = :id AND property_id = :prop"),
        {"id": room_type_id, "prop": property_id},
    ).first()
    if owns is None:
        raise HTTPException(status_code=404, detail="Room type not found")

    data, content_type = await _read_upload(file)
    key = build_key(
        "room-types", str(property_id), str(room_type_id), content_type=content_type
    )
    try:
        put_object(_STORE, key, data, content_type)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Best effort, and deliberately not guarded by a try/except that fails the
    # upload: the photograph is already stored, and a missing thumbnail costs a
    # slower page, not a lost picture.
    thumb = put_thumbnail(_STORE, key, data)

    photo_id = _insert_photo(
        db, table="room_type_photos", owner_col="room_type_id",
        owner_id=room_type_id, property_id=property_id, key=key,
        content_type=content_type, size=len(data), caller=caller, caption=caption,
        make_primary=make_primary, thumb_key=thumb,
    )
    record_audit(
        db, action="room_type.photo.add", entity_type="room_type",
        entity_id=str(room_type_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"photo_id": str(photo_id), "size_bytes": len(data),
               "content_type": content_type},
    )
    return get_room_type_managed(room_type_id, property_id, caller, db)


@rooms_router.post(
    "/room-types/manage/{room_type_id}/photos/{photo_id}/primary",
    response_model=rs.RoomTypeDetailOut,
)
def set_room_type_primary_photo(
    room_type_id: uuid.UUID,
    photo_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Promote one photo to primary; a partial unique index enforces the rest."""
    assert_property_in_org(db, caller, property_id)
    # Clear first: the index allows only one primary row per room type.
    db.execute(
        text(
            "UPDATE property.room_type_photos SET is_primary = false "
            "WHERE room_type_id = :rt AND is_primary"
        ),
        {"rt": room_type_id},
    )
    result = db.execute(
        text(
            "UPDATE property.room_type_photos SET is_primary = true "
            "WHERE id = :id AND room_type_id = :rt AND property_id = :prop"
        ),
        {"id": photo_id, "rt": room_type_id, "prop": property_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Photo not found")
    record_audit(
        db, action="room_type.photo.primary", entity_type="room_type",
        entity_id=str(room_type_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"photo_id": str(photo_id)},
    )
    return get_room_type_managed(room_type_id, property_id, caller, db)


@rooms_router.delete(
    "/room-types/manage/{room_type_id}/photos/{photo_id}",
    response_model=rs.RoomTypeDetailOut,
)
def delete_room_type_photo(
    room_type_id: uuid.UUID,
    photo_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT storage_key, is_primary FROM property.room_type_photos
            WHERE id = :id AND room_type_id = :rt AND property_id = :prop
            """
        ),
        {"id": photo_id, "rt": room_type_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Photo not found")

    db.execute(
        text("DELETE FROM property.room_type_photos WHERE id = :id"), {"id": photo_id}
    )
    # Losing the primary must not leave the type without one.
    if row["is_primary"]:
        db.execute(
            text(
                """
                UPDATE property.room_type_photos SET is_primary = true
                WHERE id = (
                    SELECT id FROM property.room_type_photos
                    WHERE room_type_id = :rt ORDER BY sort_order, created_at LIMIT 1
                )
                """
            ),
            {"rt": room_type_id},
        )
    # Drop the blob only after the row is gone, so a storage failure cannot
    # leave a row pointing at an object that no longer exists.
    if row["storage_key"]:
        try:
            delete_object(_STORE, row["storage_key"])
        except ObjectStoreError:
            pass

    record_audit(
        db, action="room_type.photo.delete", entity_type="room_type",
        entity_id=str(room_type_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={"photo_id": str(photo_id)},
    )
    return get_room_type_managed(room_type_id, property_id, caller, db)


# --------------------------------------------------------------------------
# Room photos (screen 059 "Photos") — same storage path as room-type photos
# --------------------------------------------------------------------------
@rooms_router.post(
    "/rooms/{room_id}/photos",
    response_model=rs.RoomDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_room_photo(
    room_id: uuid.UUID,
    property_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
    make_primary: bool = Form(default=False),
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Upload one room photo. ``make_primary`` replaces the hero image."""
    assert_property_in_org(db, caller, property_id)
    owns = db.execute(
        text("SELECT 1 FROM property.rooms WHERE id = :id AND property_id = :prop"),
        {"id": room_id, "prop": property_id},
    ).first()
    if owns is None:
        raise HTTPException(status_code=404, detail="Room not found")

    data, content_type = await _read_upload(file)
    key = build_key("rooms", str(property_id), str(room_id), content_type=content_type)
    try:
        put_object(_STORE, key, data, content_type)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    thumb = put_thumbnail(_STORE, key, data)

    photo_id = _insert_photo(
        db, table="room_photos", owner_col="room_id", owner_id=room_id,
        property_id=property_id, key=key, content_type=content_type,
        size=len(data), caller=caller, caption=caption, make_primary=make_primary,
        thumb_key=thumb,
    )
    record_audit(
        db, action="room.photo.add", entity_type="room", entity_id=str(room_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={"photo_id": str(photo_id), "size_bytes": len(data),
               "content_type": content_type},
    )
    return get_room(room_id, property_id, caller, db)


@rooms_router.post(
    "/rooms/{room_id}/photos/{photo_id}/primary", response_model=rs.RoomDetailOut
)
def set_room_primary_photo(
    room_id: uuid.UUID,
    photo_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    # Clear first: the partial unique index allows one primary per room.
    db.execute(
        text(
            "UPDATE property.room_photos SET is_primary = false "
            "WHERE room_id = :r AND is_primary"
        ),
        {"r": room_id},
    )
    result = db.execute(
        text(
            "UPDATE property.room_photos SET is_primary = true "
            "WHERE id = :id AND room_id = :r AND property_id = :prop"
        ),
        {"id": photo_id, "r": room_id, "prop": property_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Photo not found")
    record_audit(
        db, action="room.photo.primary", entity_type="room", entity_id=str(room_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after={"photo_id": str(photo_id)},
    )
    return get_room(room_id, property_id, caller, db)


@rooms_router.delete(
    "/rooms/{room_id}/photos/{photo_id}", response_model=rs.RoomDetailOut
)
def delete_room_photo(
    room_id: uuid.UUID,
    photo_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT storage_key, is_primary FROM property.room_photos
            WHERE id = :id AND room_id = :r AND property_id = :prop
            """
        ),
        {"id": photo_id, "r": room_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Photo not found")

    db.execute(text("DELETE FROM property.room_photos WHERE id = :id"), {"id": photo_id})
    # Losing the primary must not leave the room without one.
    if row["is_primary"]:
        db.execute(
            text(
                """
                UPDATE property.room_photos SET is_primary = true
                WHERE id = (
                    SELECT id FROM property.room_photos
                    WHERE room_id = :r ORDER BY sort_order, created_at LIMIT 1
                )
                """
            ),
            {"r": room_id},
        )
    if row["storage_key"]:
        try:
            delete_object(_STORE, row["storage_key"])
        except ObjectStoreError:
            pass

    record_audit(
        db, action="room.photo.delete", entity_type="room", entity_id=str(room_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before={"photo_id": str(photo_id)},
    )
    return get_room(room_id, property_id, caller, db)


# --------------------------------------------------------------------------
# Bulk create (onboarding step 4)
# --------------------------------------------------------------------------
MAX_BULK_ROOMS = 200


def _resolve_floor(
    db: Session, *, property_id: uuid.UUID, floor_id: uuid.UUID
) -> dict[str, Any]:
    """The floor, its building, and the names that go with them.

    ``property.rooms`` carries both foreign keys (``building_id``,
    ``floor_id``) and free-text display columns (``building``, ``floor``). The
    estate screens count rooms by ``floor_id``; the room grid shows the text.
    Writing one without the other is how a room ends up sitting on a floor that
    the structure screen reports as empty -- so every write goes through here
    and sets all four from the floor record itself.
    """
    row = db.execute(
        text(
            """
            SELECT f.id AS floor_id, f.name AS floor_name,
                   b.id AS building_id, b.name AS building_name
            FROM property.floors f
            JOIN property.buildings b ON b.id = f.building_id
            WHERE f.id = :id AND f.property_id = :prop
            """
        ),
        {"id": floor_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=422, detail="That floor is not in this property.")
    return dict(row)


def parse_room_spec(spec: str) -> list[str]:
    """Turn "101-110, 201, 203" into the room codes it means.

    Ranges only expand when both ends are plain numbers of the same width, so
    "101-110" gives 101..110 while "A1-A9" is treated as one literal code
    rather than guessed at. Codes keep their leading zeros, because "007" and
    "7" are different rooms on a door.
    """
    codes: list[str] = []
    seen: set[str] = set()
    for part in (p.strip() for p in spec.replace(";", ",").split(",")):
        if not part:
            continue
        lo, sep, hi = part.partition("-")
        lo, hi = lo.strip(), hi.strip()
        if sep and lo.isdigit() and hi.isdigit() and len(lo) == len(hi):
            start, end = int(lo), int(hi)
            if end < start:
                raise HTTPException(
                    status_code=422,
                    detail=f"'{part}' counts backwards — the range must ascend.",
                )
            if end - start + 1 > MAX_BULK_ROOMS:
                raise HTTPException(
                    status_code=422,
                    detail=f"'{part}' is more than {MAX_BULK_ROOMS} rooms.",
                )
            width = len(lo)
            for n in range(start, end + 1):
                code = str(n).zfill(width)
                if code not in seen:
                    seen.add(code)
                    codes.append(code)
        else:
            if part not in seen:
                seen.add(part)
                codes.append(part)

    if not codes:
        raise HTTPException(status_code=422, detail="No room numbers given.")
    if len(codes) > MAX_BULK_ROOMS:
        raise HTTPException(
            status_code=422,
            detail=f"That is {len(codes)} rooms; {MAX_BULK_ROOMS} at a time is the limit.",
        )
    return codes


@rooms_router.post("/rooms/bulk-create", response_model=rs.BulkCreateOut,
                   status_code=status.HTTP_201_CREATED)
def bulk_create_rooms(
    body: rs.BulkCreateIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    """Create a numbered run of rooms in one go.

    Partial success on purpose. A property typing in "101-110" when 105 already
    exists wants the other nine, not an error and an empty floor -- so an
    existing code is skipped and reported rather than failing the batch.
    """
    assert_property_in_org(db, caller, property_id)

    owns_type = db.execute(
        text("SELECT 1 FROM property.room_types WHERE id = :rt AND property_id = :p"),
        {"rt": body.room_type_id, "p": property_id},
    ).first()
    if owns_type is None:
        raise HTTPException(
            status_code=422, detail="That room type is not in this property.")

    # A floor id pins the room to the structure; the names follow from it.
    place = (_resolve_floor(db, property_id=property_id, floor_id=body.floor_id)
             if body.floor_id else None)
    building_name = place["building_name"] if place else body.building
    floor_name = place["floor_name"] if place else body.floor

    codes = parse_room_spec(body.spec)
    # Which room type already holds each taken code. A room number is unique
    # across the property, so "already exists" on its own leaves the operator
    # hunting for where -- naming the type answers it in the same breath.
    taken = {
        r[0]: r[1] for r in db.execute(
            text(
                "SELECT rm.code, rt.name FROM property.rooms rm "
                "JOIN property.room_types rt ON rt.id = rm.room_type_id "
                "WHERE rm.property_id = :p AND rm.code = ANY(:codes)"
            ),
            {"p": property_id, "codes": codes},
        ).all()
    }

    results: list[rs.BulkCreateRoomResult] = []
    made = 0
    for code in codes:
        if code in taken:
            results.append(rs.BulkCreateRoomResult(
                code=code, created=False,
                reason=f"Already exists in {taken[code]}"))
            continue
        db.execute(
            text(
                """
                INSERT INTO property.rooms
                    (id, organization_id, property_id, room_type_id, code,
                     building_id, floor_id, building, floor, bed_setup,
                     max_adults, max_children,
                     base_rate, status, service_status)
                VALUES (gen_random_uuid(), :org, :prop, :rt, :code,
                        :building_id, :floor_id, :building, :floor, :bed,
                        :adults, :children,
                        :rate, 'active', 'in_service')
                """
            ),
            {"org": caller.organization_id, "prop": property_id,
             "rt": body.room_type_id, "code": code,
             "building_id": place["building_id"] if place else None,
             "floor_id": place["floor_id"] if place else None,
             "building": building_name, "floor": floor_name,
             "bed": body.bed_setup, "adults": body.max_adults,
             "children": body.max_children, "rate": body.base_rate},
        )
        made += 1
        results.append(rs.BulkCreateRoomResult(code=code, created=True))

    if made:
        # The type can now sell more rooms than it could a moment ago.
        sync_capacity(db, property_id=property_id, room_type_id=body.room_type_id)
        record_audit(
            db, action="room.bulk_create", entity_type="room",
            entity_id=str(property_id),
            organization_id=caller.organization_id, property_id=property_id,
            actor_subject=caller.subject,
            after={"spec": body.spec, "created": made,
                   "codes": [r.code for r in results if r.created][:50]},
        )

    return rs.BulkCreateOut(
        created=made, skipped=len(results) - made, results=results)

# ============================================ booking page branding ========


def _branding_out(row) -> rs.BrandingOut:
    if row is None:
        return rs.BrandingOut()
    palette = theme(row["brand_color"])
    return rs.BrandingOut(
        tagline=row["tagline"],
        logo_url=photo_url(None, row["logo_key"], None),
        banner_url=photo_url(None, row["banner_key"], None),
        updated_at=row["updated_at"],
        **palette,
    )


@rooms_router.get("/properties/{property_id}/booking-branding",
            response_model=rs.BrandingOut)
def get_booking_branding(
    property_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_permission("distribution", "view")),
):
    """How this property's booking page looks today."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text("SELECT brand_color, tagline, logo_key, banner_key, updated_at "
             "FROM property.booking_branding WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()
    return _branding_out(row)


@rooms_router.put("/properties/{property_id}/booking-branding",
            response_model=rs.BrandingOut)
def set_booking_branding(
    property_id: uuid.UUID,
    body: rs.BrandingIn,
    db: Session = Depends(get_session),
    caller: Caller = Depends(
        require_permission("distribution", "configure")),
):
    """Change how this property's booking page looks.

    Gated on ``distribution:configure`` -- the same permission that puts the
    property on sale, because this is the same subject: what the public sees
    when they arrive. Not ``property:update``, which is addresses and phone
    numbers.

    The colour is validated rather than stored as given. See branding.py: the
    text colour that goes on top is computed from it, and a colour that can
    carry neither white nor near-black is refused with the measurement, since
    "that shade is unreadable" is far more useful than a red field.
    """
    # assert_property_in_org has just proven this property belongs to the
    # caller's organisation, so the caller's own org is the right one to
    # stamp on the row -- reading it back from the property would be a second
    # query answering a question already settled.
    assert_property_in_org(db, caller, property_id)

    try:
        colour = validate_colour(body.brand_color)
    except UnusableColour as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    tagline = (body.tagline or "").strip() or None
    row = db.execute(
        text(
            """
            INSERT INTO property.booking_branding
                (property_id, organization_id, brand_color, tagline,
                 logo_key, banner_key, updated_by)
            VALUES (:p, :o, :c, :t, :logo, :banner, :u)
            ON CONFLICT (property_id) DO UPDATE
               SET brand_color = EXCLUDED.brand_color,
                   tagline = EXCLUDED.tagline,
                   logo_key = EXCLUDED.logo_key,
                   banner_key = EXCLUDED.banner_key,
                   updated_by = EXCLUDED.updated_by,
                   updated_at = now()
            RETURNING brand_color, tagline, logo_key, banner_key, updated_at
            """
        ),
        {"p": property_id, "o": caller.organization_id, "c": colour,
         "t": tagline, "logo": body.logo_key, "banner": body.banner_key,
         "u": caller.user_id},
    ).mappings().first()

    # What a guest sees is worth an audit row: a booking page that suddenly
    # looks wrong is something somebody asks about afterwards.
    record_audit(
        db,
        action="booking_branding_set",
        entity_type="property",
        entity_id=str(property_id),
        organization_id=caller.organization_id,
        property_id=property_id,
        actor_subject=caller.subject,
        after={"brand_color": colour, "tagline": tagline,
               "has_logo": bool(body.logo_key),
               "has_banner": bool(body.banner_key)},
    )
    return _branding_out(row)

