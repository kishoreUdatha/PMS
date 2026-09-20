"""Buildings & Floors API (screen 061).

Deny-by-default like the rest of the property module: an active membership plus
a scoped ``rooms.*`` permission, and the target property must belong to the
caller's organization. Mutations are audited and optimistically locked.

The rule worth stating: a floor cannot be deactivated while its rooms hold live
reservations. The screen asks for that state up front (``can_deactivate``) so it
can explain the block, and the endpoint enforces it again on write — the check
is not a UI courtesy.
"""

from __future__ import annotations

import uuid
from typing import Any

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import estate_schemas as es
from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

estate_router = APIRouter(tags=["buildings-floors"], route_class=TransactionalRoute)


def _conflict(entity: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"{entity} was modified by someone else. Reload and try again.",
    )


def _check_status(value: str | None) -> None:
    if value is not None and value not in es.STATUSES:
        raise HTTPException(
            status_code=422, detail=f"Invalid status: {value!r}. Use active or inactive."
        )


def _check_range(from_no: str | None, to_no: str | None) -> None:
    """Reject a backwards numeric range.

    Room numbers are free text ("A201"), so this only applies when both ends
    parse as integers — otherwise the range is whatever the property calls it.
    """
    if not from_no or not to_no:
        return
    try:
        start, end = int(from_no), int(to_no)
    except ValueError:
        return
    if end < start:
        raise HTTPException(
            status_code=422,
            detail=f"To Room No. ({to_no}) is before From Room No. ({from_no})",
        )



# Rooms on a floor whose reservations are still live (not cancelled, not
# departed) — the thing that blocks deactivation.
_ACTIVE_RES_SQL = """
    SELECT count(*)
    FROM booking.reservation_units ru
    JOIN property.rooms r ON r.id = ru.assigned_room_id
    WHERE r.floor_id = :floor
      AND ru.status NOT IN ('cancelled', 'checked_out', 'no_show')
      AND ru.departure_date >= CURRENT_DATE
"""


def _floors_for(db: Session, property_id: uuid.UUID) -> dict[uuid.UUID, list[es.FloorOut]]:
    rows = db.execute(
        text(
            """
            SELECT f.id, f.building_id, f.code, f.name, f.from_room_no,
                   f.to_room_no, f.display_order, f.status, f.version,
                   f.created_at, f.updated_at,
                   cu.display_name AS created_by_name,
                   uu.display_name AS updated_by_name,
                   (SELECT count(*) FROM property.rooms r
                     WHERE r.floor_id = f.id) AS room_count
            FROM property.floors f
            LEFT JOIN iam.users cu ON cu.id = f.created_by
            LEFT JOIN iam.users uu ON uu.id = f.updated_by
            WHERE f.property_id = :prop
            ORDER BY f.display_order, f.name
            """
        ),
        {"prop": property_id},
    ).mappings().all()
    out: dict[uuid.UUID, list[es.FloorOut]] = {}
    for r in rows:
        out.setdefault(r["building_id"], []).append(es.FloorOut(**r))
    return out


@estate_router.get("/buildings", response_model=list[es.BuildingOut])
def list_buildings(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The building/floor tree down the left of screen 061."""
    assert_property_in_org(db, caller, property_id)
    floors = _floors_for(db, property_id)
    rows = db.execute(
        text(
            """
            SELECT b.id, b.code, b.name, b.display_order, b.status, b.version,
                   b.created_at, b.updated_at,
                   cu.display_name AS created_by_name,
                   uu.display_name AS updated_by_name
            FROM property.buildings b
            LEFT JOIN iam.users cu ON cu.id = b.created_by
            LEFT JOIN iam.users uu ON uu.id = b.updated_by
            WHERE b.property_id = :prop
            ORDER BY b.display_order, b.name
            """
        ),
        {"prop": property_id},
    ).mappings().all()
    return [
        es.BuildingOut(
            **r,
            floors=floors.get(r["id"], []),
            floor_count=len(floors.get(r["id"], [])),
            room_count=sum(f.room_count for f in floors.get(r["id"], [])),
        )
        for r in rows
    ]


@estate_router.post(
    "/buildings", response_model=es.BuildingOut, status_code=status.HTTP_201_CREATED
)
def create_building(
    body: es.BuildingIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_status(body.status)
    building_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.buildings
                    (id, organization_id, property_id, code, name,
                     display_order, status, created_by, updated_by)
                VALUES (:id, :org, :prop, :code, :name, :ord, :status,
                        :actor, :actor)
                """
            ),
            {
                "id": building_id, "org": caller.organization_id,
                "prop": property_id, "code": body.code, "name": body.name,
                "ord": body.display_order, "status": body.status,
                "actor": caller.user_id,
            },
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Building code {body.code} already exists"
        ) from exc

    record_audit(
        db, action="building.create", entity_type="building",
        entity_id=str(building_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"code": body.code, "name": body.name, "status": body.status},
    )
    return next(b for b in list_buildings(property_id, caller, db) if b.id == building_id)


@estate_router.put("/buildings/{building_id}", response_model=es.BuildingOut)
def update_building(
    building_id: uuid.UUID,
    property_id: uuid.UUID,
    body: es.BuildingUpdate,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_status(body.status)

    before = db.execute(
        text(
            "SELECT code, name, display_order, status, version "
            "FROM property.buildings WHERE id = :id AND property_id = :prop"
        ),
        {"id": building_id, "prop": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Building not found")

    # Deactivating a building takes its floors with it, so it needs the same
    # protection a floor gets.
    if body.status == "inactive":
        blocked = db.execute(
            text(
                """
                SELECT count(*)
                FROM booking.reservation_units ru
                JOIN property.rooms r ON r.id = ru.assigned_room_id
                WHERE r.building_id = :b
                  AND ru.status NOT IN ('cancelled', 'checked_out', 'no_show')
                  AND ru.departure_date >= CURRENT_DATE
                """
            ),
            {"b": building_id},
        ).scalar_one()
        if blocked:
            raise HTTPException(
                status_code=409,
                detail=f"{blocked} active reservation(s) still use rooms in this "
                       "building. Move or release them first.",
            )

    fields = {
        "name": body.name, "display_order": body.display_order,
        "status": body.status,
    }
    sets = [f"{c} = :{c}" for c, v in fields.items() if v is not None]
    params = {c: v for c, v in fields.items() if v is not None}
    params.update({
        "id": building_id, "prop": property_id, "version": body.version,
        "actor": caller.user_id,
    })
    result = db.execute(
        text(
            f"""
            UPDATE property.buildings
               SET {', '.join([*sets, 'updated_at = now()', 'updated_by = :actor',
                               'version = version + 1'])}
             WHERE id = :id AND property_id = :prop AND version = :version
            """
        ),
        params,
    )
    if result.rowcount == 0:
        raise _conflict("Building")

    record_audit(
        db, action="building.update", entity_type="building",
        entity_id=str(building_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before=dict(before), after={k: str(v) for k, v in params.items()
                                    if k not in ("id", "prop", "version", "actor")},
        reason=body.reason,
    )
    return next(b for b in list_buildings(property_id, caller, db) if b.id == building_id)


# --------------------------------------------------------------------------
# Floors
# --------------------------------------------------------------------------
@estate_router.get("/floors/{floor_id}", response_model=es.FloorDetailOut)
def get_floor(
    floor_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Floor detail for the editor, including why it may not be deactivated."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT f.id, f.building_id, f.code, f.name, f.from_room_no,
                   f.to_room_no, f.display_order, f.status, f.version,
                   f.created_at, f.updated_at,
                   b.name AS building_name,
                   cu.display_name AS created_by_name,
                   uu.display_name AS updated_by_name,
                   (SELECT count(*) FROM property.rooms r
                     WHERE r.floor_id = f.id) AS room_count
            FROM property.floors f
            JOIN property.buildings b ON b.id = f.building_id
            LEFT JOIN iam.users cu ON cu.id = f.created_by
            LEFT JOIN iam.users uu ON uu.id = f.updated_by
            WHERE f.id = :id AND f.property_id = :prop
            """
        ),
        {"id": floor_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Floor not found")

    active = db.execute(text(_ACTIVE_RES_SQL), {"floor": floor_id}).scalar_one()
    blocker = None
    if active:
        blocker = (
            f"This floor has {row['room_count']} room(s) and {active} active "
            "reservation(s). Move or release the rooms first."
        )
    return es.FloorDetailOut(
        **row, active_reservations=active, can_deactivate=active == 0,
        blocker_message=blocker,
    )


@estate_router.get("/floors/{floor_id}/dependents", response_model=list[es.DependentRoom])
def floor_dependents(
    floor_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Screen 061 'View Dependent Rooms'."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT r.id, r.code, rt.name AS room_type_name, r.status,
                   r.service_status,
                   (SELECT count(*) FROM booking.reservation_units ru
                     WHERE ru.assigned_room_id = r.id
                       AND ru.status NOT IN ('cancelled','checked_out','no_show')
                       AND ru.departure_date >= CURRENT_DATE) AS active_reservations
            FROM property.rooms r
            JOIN property.room_types rt ON rt.id = r.room_type_id
            WHERE r.floor_id = :id AND r.property_id = :prop
            ORDER BY r.code
            """
        ),
        {"id": floor_id, "prop": property_id},
    ).mappings().all()
    return [es.DependentRoom(**r) for r in rows]


@estate_router.post(
    "/floors", response_model=es.FloorDetailOut, status_code=status.HTTP_201_CREATED
)
def create_floor(
    body: es.FloorIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_status(body.status)
    _check_range(body.from_room_no, body.to_room_no)
    owns = db.execute(
        text("SELECT 1 FROM property.buildings WHERE id = :b AND property_id = :prop"),
        {"b": body.building_id, "prop": property_id},
    ).first()
    if owns is None:
        raise HTTPException(status_code=422, detail="Building not in this property")

    floor_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.floors
                    (id, organization_id, property_id, building_id, code, name,
                     from_room_no, to_room_no, display_order, status,
                     created_by, updated_by)
                VALUES (:id, :org, :prop, :b, :code, :name, :from, :to, :ord,
                        :status, :actor, :actor)
                """
            ),
            {
                "id": floor_id, "org": caller.organization_id, "prop": property_id,
                "b": body.building_id, "code": body.code, "name": body.name,
                "from": body.from_room_no, "to": body.to_room_no,
                "ord": body.display_order, "status": body.status,
                "actor": caller.user_id,
            },
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Floor code {body.code} already exists in this building",
        ) from exc

    record_audit(
        db, action="floor.create", entity_type="floor", entity_id=str(floor_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={"code": body.code, "name": body.name,
               "building_id": str(body.building_id)},
    )
    return get_floor(floor_id, property_id, caller, db)


@estate_router.put("/floors/{floor_id}", response_model=es.FloorDetailOut)
def update_floor(
    floor_id: uuid.UUID,
    property_id: uuid.UUID,
    body: es.FloorUpdate,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_status(body.status)
    _check_range(body.from_room_no, body.to_room_no)

    before = db.execute(
        text(
            "SELECT code, name, from_room_no, to_room_no, display_order, status, "
            "version FROM property.floors WHERE id = :id AND property_id = :prop"
        ),
        {"id": floor_id, "prop": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Floor not found")

    if body.status == "inactive":
        active = db.execute(text(_ACTIVE_RES_SQL), {"floor": floor_id}).scalar_one()
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"{active} active reservation(s) still use rooms on this "
                       "floor. Move or release them first.",
            )

    fields = {
        "name": body.name, "code": body.code, "from_room_no": body.from_room_no,
        "to_room_no": body.to_room_no, "display_order": body.display_order,
        "status": body.status,
    }
    sets = [f"{c} = :{c}" for c, v in fields.items() if v is not None]
    params = {c: v for c, v in fields.items() if v is not None}
    params.update({
        "id": floor_id, "prop": property_id, "version": body.version,
        "actor": caller.user_id,
    })
    try:
        result = db.execute(
            text(
                f"""
                UPDATE property.floors
                   SET {', '.join([*sets, 'updated_at = now()',
                                   'updated_by = :actor', 'version = version + 1'])}
                 WHERE id = :id AND property_id = :prop AND version = :version
                """
            ),
            params,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Floor code already exists in this building"
        ) from exc
    if result.rowcount == 0:
        raise _conflict("Floor")

    # The legacy free-text column on rooms mirrors the floor code, so the Rooms
    # screen's filters keep agreeing with the tree after a rename.
    if body.code is not None:
        db.execute(
            text("UPDATE property.rooms SET floor = :code WHERE floor_id = :id"),
            {"code": body.code, "id": floor_id},
        )

    record_audit(
        db, action="floor.update", entity_type="floor", entity_id=str(floor_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before=dict(before),
        after={k: str(v) for k, v in params.items()
               if k not in ("id", "prop", "version", "actor")},
        reason=body.reason,
    )
    return get_floor(floor_id, property_id, caller, db)


@estate_router.get("/rooms/unassigned",
                   response_model=list[es.UnassignedRoom])
def unassigned_rooms(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Rooms that belong to no floor.

    These are invisible on the Buildings & Floors tree — a floor can only list
    what points at it — so without this they are lost: real, bookable rooms
    that no part of the estate screen admits exist.
    """
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT r.id, r.code, rt.name AS room_type,
                   r.floor AS floor_hint, r.building AS building_hint
            FROM property.rooms r
            LEFT JOIN property.room_types rt ON rt.id = r.room_type_id
            WHERE r.property_id = :prop AND r.floor_id IS NULL
            ORDER BY r.code
            """
        ),
        {"prop": property_id},
    ).mappings().all()
    return [es.UnassignedRoom(**dict(r)) for r in rows]


@estate_router.post("/floors/{floor_id}/assign-rooms",
                    response_model=es.FloorDetailOut)
def assign_rooms(
    floor_id: uuid.UUID,
    property_id: uuid.UUID,
    body: es.AssignRoomsIn,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Put the named rooms on this floor, wherever they are now.

    ``move-rooms`` below moves rooms *off* a floor, so it can never reach a
    room that is on no floor: its WHERE never matches NULL. This is the other
    direction — pull rooms onto a floor — and it is what makes a room without
    a floor recoverable.

    The room's building follows the floor's, because a room cannot be on the
    third floor of one building and inside another.
    """
    assert_property_in_org(db, caller, property_id)
    floor = db.execute(
        text("SELECT f.id, f.building_id, f.code, b.name AS building_name "
             "FROM property.floors f "
             "JOIN property.buildings b ON b.id = f.building_id "
             "WHERE f.id = :id AND f.property_id = :prop"),
        {"id": floor_id, "prop": property_id},
    ).mappings().first()
    if floor is None:
        raise HTTPException(status_code=404, detail="Floor not found")

    # Rooms must belong to this property. Silently skipping a stray id would
    # report a success that moved nothing.
    found = {
        r[0] for r in db.execute(
            text("SELECT id FROM property.rooms "
                 "WHERE property_id = :prop AND id = ANY(:ids)"),
            {"prop": property_id, "ids": body.room_ids},
        )
    }
    missing = set(body.room_ids) - found
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"{len(missing)} of those rooms do not belong to this "
                   f"property.")

    moved = db.execute(
        text(
            """
            UPDATE property.rooms
               SET floor_id = :floor, building_id = :building, floor = :code,
                   building = :bname,
                   updated_at = now(), version = version + 1
             WHERE property_id = :prop AND id = ANY(:ids)
            """
        ),
        {"floor": floor["id"], "building": floor["building_id"],
         "code": floor["code"], "bname": floor["building_name"],
         "prop": property_id, "ids": body.room_ids},
    ).rowcount

    record_audit(
        db, action="floor.assign_rooms", entity_type="floor",
        entity_id=str(floor_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"to_floor_id": str(floor_id), "rooms_assigned": moved},
        reason=body.reason,
    )
    return get_floor(floor_id, property_id, caller, db)


@estate_router.post("/floors/{floor_id}/move-rooms", response_model=es.FloorDetailOut)
def move_rooms(
    floor_id: uuid.UUID,
    property_id: uuid.UUID,
    body: es.MoveRoomsIn,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Screen 061 'Move Rooms' — reassign rooms to another floor."""
    assert_property_in_org(db, caller, property_id)
    if body.target_floor_id == floor_id:
        raise HTTPException(status_code=422, detail="Target floor is the same floor")

    target = db.execute(
        text(
            "SELECT f.id, f.building_id, f.code, b.name AS building_name "
            "FROM property.floors f "
            "JOIN property.buildings b ON b.id = f.building_id "
            "WHERE f.id = :id AND f.property_id = :prop"
        ),
        {"id": body.target_floor_id, "prop": property_id},
    ).mappings().first()
    if target is None:
        raise HTTPException(status_code=404, detail="Target floor not found")

    params: dict[str, Any] = {
        "floor": floor_id, "target": target["id"],
        "building": target["building_id"], "code": target["code"],
        "bname": target["building_name"],
    }
    room_filter = ""
    if body.room_ids:
        room_filter = " AND id = ANY(:ids)"
        params["ids"] = body.room_ids

    moved = db.execute(
        text(
            f"""
            UPDATE property.rooms
               SET floor_id = :target, building_id = :building, floor = :code,
                   building = :bname,
                   updated_at = now(), version = version + 1
             WHERE floor_id = :floor{room_filter}
            """
        ),
        params,
    ).rowcount

    record_audit(
        db, action="floor.move_rooms", entity_type="floor", entity_id=str(floor_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        before={"from_floor_id": str(floor_id)},
        after={"to_floor_id": str(body.target_floor_id), "rooms_moved": moved},
        reason=body.reason,
    )
    return get_floor(floor_id, property_id, caller, db)


@estate_router.post("/buildings/reorder", response_model=list[es.BuildingOut])
def reorder_buildings(
    body: es.ReorderIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    for position, building_id in enumerate(body.ids):
        db.execute(
            text(
                "UPDATE property.buildings SET display_order = :ord, "
                "updated_at = now(), updated_by = :actor "
                "WHERE id = :id AND property_id = :prop"
            ),
            {"ord": position, "id": building_id, "prop": property_id,
             "actor": caller.user_id},
        )
    record_audit(
        db, action="building.reorder", entity_type="building", entity_id=None,
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after={"order": [str(i) for i in body.ids]},
    )
    return list_buildings(property_id, caller, db)


@estate_router.post("/floors/reorder", response_model=list[es.BuildingOut])
def reorder_floors(
    body: es.ReorderIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    for position, floor_id in enumerate(body.ids):
        db.execute(
            text(
                "UPDATE property.floors SET display_order = :ord, "
                "updated_at = now(), updated_by = :actor "
                "WHERE id = :id AND property_id = :prop"
            ),
            {"ord": position, "id": floor_id, "prop": property_id,
             "actor": caller.user_id},
        )
    record_audit(
        db, action="floor.reorder", entity_type="floor", entity_id=None,
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after={"order": [str(i) for i in body.ids]},
    )
    return list_buildings(property_id, caller, db)


# --------------------------------------------------------------------------
# Removing structure
# --------------------------------------------------------------------------
@estate_router.delete("/floors/{floor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_floor(
    floor_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Remove a floor that holds no rooms.

    A floor is where rooms live, so deleting one that still has them would
    either orphan the rooms or silently take them off the map. It refuses and
    says how many, because the answer to "why can I not delete this" should
    not require going and counting.
    """
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text("SELECT f.id, f.name, f.building_id FROM property.floors f "
             "WHERE f.id = :id AND f.property_id = :prop"),
        {"id": floor_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Floor not found")

    rooms = db.execute(
        text("SELECT count(*) FROM property.rooms WHERE floor_id = :id"),
        {"id": floor_id},
    ).scalar_one()
    if rooms:
        raise HTTPException(
            status_code=409,
            detail=f"{row['name']} still has {rooms} room"
                   f"{'' if rooms == 1 else 's'}. Move or remove them first.",
        )

    db.execute(text("DELETE FROM property.floors WHERE id = :id"), {"id": floor_id})
    record_audit(
        db, action="floor.delete", entity_type="floor", entity_id=str(floor_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before={"name": row["name"]},
    )


@estate_router.delete("/buildings/{building_id}",
                      status_code=status.HTTP_204_NO_CONTENT)
def delete_building(
    building_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "configure")),
    db: Session = Depends(get_session),
):
    """Remove a building that holds no floors and no rooms.

    Floors are not cascaded away with it. A building with floors is a
    structure somebody built deliberately, and deleting five floors because
    one click was aimed at the row above them is not a recoverable mistake --
    the floors have to go first, which makes their loss a decision each time.
    """
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text("SELECT id, name FROM property.buildings "
             "WHERE id = :id AND property_id = :prop"),
        {"id": building_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Building not found")

    counts = db.execute(
        text(
            """
            SELECT (SELECT count(*) FROM property.floors
                     WHERE building_id = :id)            AS floors,
                   (SELECT count(*) FROM property.rooms
                     WHERE building_id = :id)            AS rooms
            """
        ),
        {"id": building_id},
    ).mappings().first()

    blockers = []
    if counts["floors"]:
        blockers.append(f"{counts['floors']} floor"
                        f"{'' if counts['floors'] == 1 else 's'}")
    if counts["rooms"]:
        blockers.append(f"{counts['rooms']} room"
                        f"{'' if counts['rooms'] == 1 else 's'}")
    if blockers:
        raise HTTPException(
            status_code=409,
            detail=f"{row['name']} still has {' and '.join(blockers)}. "
                   f"Remove those first.",
        )

    db.execute(text("DELETE FROM property.buildings WHERE id = :id"),
               {"id": building_id})
    record_audit(
        db, action="building.delete", entity_type="building",
        entity_id=str(building_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={"name": row["name"]},
    )
