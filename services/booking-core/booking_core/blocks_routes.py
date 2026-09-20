"""Room Block & Out of Order API (screen 063).

A block holds a room out of sale for a date range. The overlap rule is *not*
reimplemented here: creating a block inserts a ``booking.room_calendar_entries``
row of kind 'maintenance', and the GiST exclusion constraint already on that
table rejects it if the room is taken by a reservation or another block for any
part of the period (§4). The database is the arbiter; this module translates
its refusal into a per-room conflict the screen can show.

Blocking several rooms at once is therefore partially successful by design: the
rooms that are free get blocked, the ones that clash come back as conflicts
rather than failing the whole request.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import blocks_schemas as bs
from .database import get_session
from .history_routes import record_status_event
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

blocks_router = APIRouter(tags=["room-blocks"], route_class=TransactionalRoute)

BLOCK_TYPE_LABELS = {"room_block": "Room Block", "out_of_order": "Out of Order"}


def _validate(value: str | None, allowed, field: str) -> None:
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


def _rooms_label(codes: list[str]) -> str:
    """"101 - 104" for a contiguous numeric run, else a comma list."""
    if not codes:
        return "—"
    if len(codes) == 1:
        return codes[0]
    try:
        nums = sorted(int(c) for c in codes)
    except ValueError:
        return ", ".join(sorted(codes))
    if nums[-1] - nums[0] == len(nums) - 1:
        return f"{nums[0]} - {nums[-1]}"
    return ", ".join(str(n) for n in nums)


def _reserve_calendar(
    db: Session, *, caller: Caller, property_id: uuid.UUID, room_id: uuid.UUID,
    start: date, end: date, reason: str | None,
) -> uuid.UUID:
    """Claim the dates on the room calendar.

    Raises IntegrityError (exclusion violation) if the room is already taken —
    that is the check, and it is the database's, not ours. The period is
    half-open on the end date + 1 so a block that ends on the 18th covers the
    whole of the 18th.
    """
    entry_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO booking.room_calendar_entries
                (id, organization_id, property_id, room_id, kind,
                 occupied_period, status, reason)
            VALUES (:id, :org, :prop, :room, 'maintenance',
                    tstzrange(CAST(:start AS timestamptz), CAST(:end AS timestamptz), '[)'),
                    'active', :reason)
            """
        ),
        {
            "id": entry_id, "org": caller.organization_id, "prop": property_id,
            "room": room_id, "start": start, "end": end + timedelta(days=1),
            "reason": (reason or "")[:300] or None,
        },
    )
    return entry_id


def _sync_service_status(db: Session, room_ids: list[uuid.UUID]) -> None:
    """Re-derive ``rooms.service_status`` from today's blocks.

    The block record is the source of truth for availability; this keeps the
    physical flag agreeing with it so older consumers of ``service_status``
    (and the room cards) never contradict the calendar.
    """
    if not room_ids:
        return
    db.execute(
        text(
            """
            UPDATE property.rooms r
               SET service_status = COALESCE((
                     SELECT CASE
                              WHEN b.reason_category LIKE 'maintenance%' THEN 'maintenance'
                              ELSE 'out_of_service'
                            END
                     FROM booking.room_blocks b
                     WHERE b.room_id = r.id
                       AND b.status = 'active'
                       AND b.block_type = 'out_of_order'
                       AND CURRENT_DATE BETWEEN b.start_date AND b.end_date
                     LIMIT 1
                   ), 'in_service'),
                   updated_at = now()
             WHERE r.id = ANY(:ids)
            """
        ),
        {"ids": room_ids},
    )



@blocks_router.get("/room-blocks/stats", response_model=bs.BlockStatsOut)
def block_stats(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The four KPI cards."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT
              count(*) FILTER (
                WHERE block_type = 'room_block'
                  AND CURRENT_DATE BETWEEN start_date AND end_date)  AS currently_blocked,
              count(*) FILTER (
                WHERE block_type = 'out_of_order'
                  AND CURRENT_DATE BETWEEN start_date AND end_date)  AS out_of_order,
              count(*) FILTER (WHERE end_date = CURRENT_DATE)        AS due_to_end_today,
              count(DISTINCT room_id) FILTER (
                WHERE CURRENT_DATE BETWEEN start_date AND end_date)  AS total_unavailable
            FROM booking.room_blocks
            WHERE property_id = :prop AND status = 'active'
            """
        ),
        {"prop": property_id},
    ).mappings().one()
    return bs.BlockStatsOut(**row)


@blocks_router.get("/room-blocks/rooms", response_model=list[bs.BlockableRoom])
def blockable_rooms(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Rooms available to pick in the Create Block form."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT r.id, r.code, rt.name AS room_type_name, r.floor
            FROM property.rooms r
            JOIN property.room_types rt ON rt.id = r.room_type_id
            WHERE r.property_id = :prop AND r.status = 'active'
            ORDER BY r.code
            """
        ),
        {"prop": property_id},
    ).mappings().all()
    return [bs.BlockableRoom(**r) for r in rows]


_GROUP_SQL = """
    SELECT b.group_id,
           min(b.block_type)       AS block_type,
           min(b.reason_category)  AS reason_category,
           min(b.reason)           AS reason,
           min(b.severity)         AS severity,
           min(b.start_date)       AS start_date,
           max(b.end_date)         AS end_date,
           min(b.status)           AS status,
           min(b.linked_reference) AS linked_reference,
           min(b.created_at)       AS created_at,
           min(u.display_name)     AS created_by_name,
           min(rt.name)            AS room_type_name,
           array_agg(r.code ORDER BY r.code)  AS room_codes,
           array_agg(r.id   ORDER BY r.code)  AS room_ids,
           array_agg(b.id   ORDER BY r.code)  AS block_ids,
           array_agg(b.version ORDER BY r.code) AS versions,
           array_agg(b.status  ORDER BY r.code) AS statuses
    FROM booking.room_blocks b
    JOIN property.rooms r ON r.id = b.room_id
    JOIN property.room_types rt ON rt.id = r.room_type_id
    LEFT JOIN iam.users u ON u.id = b.created_by
"""


def _groups(db: Session, where: list[str], params: dict[str, Any]) -> list[bs.BlockGroupOut]:
    rows = db.execute(
        text(f"{_GROUP_SQL} WHERE {' AND '.join(where)} GROUP BY b.group_id "
             "ORDER BY min(b.start_date) DESC"),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        rooms = [
            bs.BlockRoomOut(
                id=bid, room_id=rid, room_code=code, room_type_name=r["room_type_name"],
                status=st, version=v,
            )
            for bid, rid, code, st, v in zip(
                r["block_ids"], r["room_ids"], r["room_codes"],
                r["statuses"], r["versions"], strict=True,
            )
        ]
        out.append(
            bs.BlockGroupOut(
                **{k: r[k] for k in (
                    "group_id", "block_type", "reason_category", "reason", "severity",
                    "start_date", "end_date", "status", "linked_reference",
                    "created_at", "created_by_name", "room_type_name",
                )},
                block_type_label=BLOCK_TYPE_LABELS.get(r["block_type"], r["block_type"]),
                reason_category_label=bs.REASON_CATEGORIES.get(
                    r["reason_category"], r["reason_category"]
                ),
                rooms_label=_rooms_label(list(r["room_codes"])),
                rooms=rooms,
            )
        )
    return out


@blocks_router.get("/room-blocks", response_model=list[bs.BlockGroupOut])
def list_blocks(
    property_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    block_type: str | None = None,
    room_type_id: uuid.UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    search: str | None = None,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Blocks, grouped so multi-room blocks read as one line."""
    assert_property_in_org(db, caller, property_id)
    _validate(status_filter, bs.STATUSES, "status")
    _validate(block_type, bs.BLOCK_TYPES, "block_type")

    where = ["b.property_id = :prop"]
    params: dict[str, Any] = {"prop": property_id}
    if status_filter:
        where.append("b.status = :status")
        params["status"] = status_filter
    if block_type:
        where.append("b.block_type = :btype")
        params["btype"] = block_type
    if room_type_id:
        where.append("r.room_type_id = :rtid")
        params["rtid"] = room_type_id
    # Overlap, not containment: a block spanning the window should appear.
    if start_date:
        where.append("b.end_date >= :from")
        params["from"] = start_date
    if end_date:
        where.append("b.start_date <= :to")
        params["to"] = end_date
    if search:
        where.append("(r.code ILIKE :q OR b.reason ILIKE :q OR b.linked_reference ILIKE :q)")
        params["q"] = f"%{search}%"
    return _groups(db, where, params)


@blocks_router.post(
    "/room-blocks", response_model=bs.BlockCreateOut, status_code=status.HTTP_201_CREATED
)
def create_block(
    body: bs.BlockCreate,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Block one or more rooms. Rooms that clash are reported, not fatal."""
    assert_property_in_org(db, caller, property_id)
    _validate(body.block_type, bs.BLOCK_TYPES, "block_type")
    _validate(body.severity, bs.SEVERITIES, "severity")
    _validate(body.reason_category, tuple(bs.REASON_CATEGORIES), "reason_category")
    if body.end_date < body.start_date:
        raise HTTPException(status_code=422, detail="End date is before start date")

    rooms = db.execute(
        text(
            """
            SELECT r.id, r.code, rt.name AS room_type_name
            FROM property.rooms r
            JOIN property.room_types rt ON rt.id = r.room_type_id
            WHERE r.property_id = :prop AND r.id = ANY(:ids)
            ORDER BY r.code
            """
        ),
        {"prop": property_id, "ids": body.room_ids},
    ).mappings().all()
    if len(rooms) != len(set(body.room_ids)):
        raise HTTPException(status_code=422, detail="One or more rooms are not in this property")

    group_id = uuid.uuid4()
    created: list[bs.BlockRoomOut] = []
    conflicts: list[bs.BlockConflict] = []

    for room in rooms:
        # Each room is its own savepoint: one clash must not roll back the rest.
        sp = db.begin_nested()
        try:
            entry_id = _reserve_calendar(
                db, caller=caller, property_id=property_id, room_id=room["id"],
                start=body.start_date, end=body.end_date, reason=body.reason,
            )
            block_id = uuid.uuid4()
            db.execute(
                text(
                    """
                    INSERT INTO booking.room_blocks
                        (id, organization_id, property_id, room_id, group_id,
                         block_type, reason_category, reason, severity,
                         start_date, end_date, linked_reference,
                         calendar_entry_id, created_by, updated_by)
                    VALUES (:id, :org, :prop, :room, :grp, :btype, :cat, :reason,
                            :sev, :start, :end, :ref, :entry, :actor, :actor)
                    """
                ),
                {
                    "id": block_id, "org": caller.organization_id,
                    "prop": property_id, "room": room["id"], "grp": group_id,
                    "btype": body.block_type, "cat": body.reason_category,
                    "reason": body.reason, "sev": body.severity,
                    "start": body.start_date, "end": body.end_date,
                    "ref": body.linked_reference, "entry": entry_id,
                    "actor": caller.user_id,
                },
            )
            sp.commit()
            created.append(
                bs.BlockRoomOut(
                    id=block_id, room_id=room["id"], room_code=room["code"],
                    room_type_name=room["room_type_name"], status="active", version=0,
                )
            )
        except IntegrityError:
            sp.rollback()
            conflicts.append(
                bs.BlockConflict(
                    room_id=room["id"], room_code=room["code"],
                    detail=f"Room {room['code']} is already reserved or blocked "
                           f"between {body.start_date} and {body.end_date}",
                )
            )

    if created:
        _sync_service_status(db, [c.room_id for c in created])
        for c in created:
            record_status_event(
                db, organization_id=caller.organization_id, property_id=property_id,
                room_id=c.room_id,
                status="out_of_order" if body.block_type == "out_of_order" else "blocked",
                source="block", changed_by=caller.user_id,
                source_reference=body.linked_reference,
                remarks=body.reason
                or f"{bs.REASON_CATEGORIES[body.reason_category]} "
                   f"({body.start_date} to {body.end_date})",
            )
        record_audit(
            db, action="room_block.create", entity_type="room_block",
            entity_id=str(group_id), organization_id=caller.organization_id,
            property_id=property_id, actor_subject=caller.subject,
            after={
                "block_type": body.block_type,
                "reason_category": body.reason_category,
                "start_date": str(body.start_date), "end_date": str(body.end_date),
                "rooms_blocked": [c.room_code for c in created],
                "rooms_in_conflict": [c.room_code for c in conflicts],
            },
            reason=body.reason,
        )
    return bs.BlockCreateOut(
        group_id=group_id if created else None, created=created, conflicts=conflicts
    )


def _release_calendar(db: Session, block_ids: list[uuid.UUID]) -> None:
    """Free the dates a block was holding."""
    db.execute(
        text(
            """
            UPDATE booking.room_calendar_entries
               SET status = 'released', updated_at = now()
             WHERE id IN (
                 SELECT calendar_entry_id FROM booking.room_blocks
                  WHERE id = ANY(:ids) AND calendar_entry_id IS NOT NULL
             )
            """
        ),
        {"ids": block_ids},
    )


@blocks_router.post("/room-blocks/{group_id}/end", response_model=list[bs.BlockGroupOut])
def end_block(
    group_id: uuid.UUID,
    property_id: uuid.UUID,
    body: bs.BlockEndIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Screen 063 'End Early'. Releases the room from the given date."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            "SELECT id, room_id, start_date, end_date FROM booking.room_blocks "
            "WHERE group_id = :g AND property_id = :prop AND status = 'active'"
        ),
        {"g": group_id, "prop": property_id},
    ).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="No active block found for this group")

    new_end = body.end_date or date.today()
    ids = [r["id"] for r in rows]
    if new_end < min(r["start_date"] for r in rows):
        raise HTTPException(
            status_code=422, detail="End date is before the block starts; cancel it instead"
        )

    _release_calendar(db, ids)
    db.execute(
        text(
            """
            UPDATE booking.room_blocks
               SET end_date = :end, status = 'ended', ended_at = now(),
                   calendar_entry_id = NULL, updated_at = now(),
                   updated_by = :actor, version = version + 1
             WHERE id = ANY(:ids)
            """
        ),
        {"end": new_end, "ids": ids, "actor": caller.user_id},
    )
    _sync_service_status(db, [r["room_id"] for r in rows])
    for r in rows:
        record_status_event(
            db, organization_id=caller.organization_id, property_id=property_id,
            room_id=r["room_id"], status="available", source="block",
            changed_by=caller.user_id,
            remarks=body.reason or f"Block ended early, effective {new_end}",
        )
    record_audit(
        db, action="room_block.end", entity_type="room_block", entity_id=str(group_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after={"end_date": str(new_end)},
        reason=body.reason,
    )
    return _groups(db, ["b.group_id = :g", "b.property_id = :prop"],
                   {"g": group_id, "prop": property_id})


@blocks_router.post("/room-blocks/{group_id}/cancel", response_model=list[bs.BlockGroupOut])
def cancel_block(
    group_id: uuid.UUID,
    property_id: uuid.UUID,
    body: bs.BlockEndIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Screen 063 'Cancel Block'. Withdraws it entirely and frees the room."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            "SELECT id, room_id FROM booking.room_blocks "
            "WHERE group_id = :g AND property_id = :prop AND status = 'active'"
        ),
        {"g": group_id, "prop": property_id},
    ).mappings().all()
    ids = [r["id"] for r in rows]
    if not ids:
        raise HTTPException(status_code=404, detail="No active block found for this group")

    _release_calendar(db, ids)
    db.execute(
        text(
            """
            UPDATE booking.room_blocks
               SET status = 'cancelled', ended_at = now(), calendar_entry_id = NULL,
                   updated_at = now(), updated_by = :actor, version = version + 1
             WHERE id = ANY(:ids)
            """
        ),
        {"ids": ids, "actor": caller.user_id},
    )
    _sync_service_status(db, [r["room_id"] for r in rows])
    for r in rows:
        record_status_event(
            db, organization_id=caller.organization_id, property_id=property_id,
            room_id=r["room_id"], status="available", source="block",
            changed_by=caller.user_id, remarks=body.reason or "Block cancelled",
        )
    record_audit(
        db, action="room_block.cancel", entity_type="room_block",
        entity_id=str(group_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject, reason=body.reason,
    )
    return _groups(db, ["b.group_id = :g", "b.property_id = :prop"],
                   {"g": group_id, "prop": property_id})


@blocks_router.put("/room-blocks/{group_id}", response_model=bs.BlockCreateOut)
def modify_block(
    group_id: uuid.UUID,
    property_id: uuid.UUID,
    body: bs.BlockUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Screen 063 'Modify Block'.

    Changing the dates means re-claiming the calendar, so the old entries are
    released and new ones inserted — and the exclusion constraint gets to
    object again. A room whose new period clashes keeps its old one and is
    reported as a conflict.
    """
    assert_property_in_org(db, caller, property_id)
    _validate(body.severity, bs.SEVERITIES, "severity")
    _validate(body.reason_category, tuple(bs.REASON_CATEGORIES), "reason_category")

    rows = db.execute(
        text(
            """
            SELECT b.id, b.room_id, b.version, b.start_date, b.end_date,
                   b.calendar_entry_id, r.code, rt.name AS room_type_name
            FROM booking.room_blocks b
            JOIN property.rooms r ON r.id = b.room_id
            JOIN property.room_types rt ON rt.id = r.room_type_id
            WHERE b.group_id = :g AND b.property_id = :prop AND b.status = 'active'
            ORDER BY r.code
            """
        ),
        {"g": group_id, "prop": property_id},
    ).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="No active block found for this group")
    if rows[0]["version"] != body.version:
        raise _conflict("Block")

    start = body.start_date or rows[0]["start_date"]
    end = body.end_date or rows[0]["end_date"]
    if end < start:
        raise HTTPException(status_code=422, detail="End date is before start date")
    dates_changed = start != rows[0]["start_date"] or end != rows[0]["end_date"]

    created: list[bs.BlockRoomOut] = []
    conflicts: list[bs.BlockConflict] = []
    for r in rows:
        sp = db.begin_nested()
        try:
            entry_id = r["calendar_entry_id"]
            if dates_changed:
                _release_calendar(db, [r["id"]])
                entry_id = _reserve_calendar(
                    db, caller=caller, property_id=property_id, room_id=r["room_id"],
                    start=start, end=end, reason=body.reason,
                )
            fields = {
                "reason_category": body.reason_category, "reason": body.reason,
                "severity": body.severity, "linked_reference": body.linked_reference,
            }
            sets = [f"{c} = :{c}" for c, v in fields.items() if v is not None]
            params = {c: v for c, v in fields.items() if v is not None}
            params.update({
                "id": r["id"], "start": start, "end": end, "entry": entry_id,
                "actor": caller.user_id,
            })
            db.execute(
                text(
                    f"""
                    UPDATE booking.room_blocks
                       SET {', '.join([*sets, 'start_date = :start', 'end_date = :end',
                                       'calendar_entry_id = :entry', 'updated_at = now()',
                                       'updated_by = :actor', 'version = version + 1'])}
                     WHERE id = :id
                    """
                ),
                params,
            )
            sp.commit()
            created.append(
                bs.BlockRoomOut(
                    id=r["id"], room_id=r["room_id"], room_code=r["code"],
                    room_type_name=r["room_type_name"], status="active",
                    version=r["version"] + 1,
                )
            )
        except IntegrityError:
            sp.rollback()
            conflicts.append(
                bs.BlockConflict(
                    room_id=r["room_id"], room_code=r["code"],
                    detail=f"Room {r['code']} is not free for {start} to {end}; "
                           "its original dates were kept",
                )
            )

    record_audit(
        db, action="room_block.update", entity_type="room_block",
        entity_id=str(group_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={"start_date": str(rows[0]["start_date"]),
                "end_date": str(rows[0]["end_date"])},
        after={"start_date": str(start), "end_date": str(end),
               "rooms_in_conflict": [c.room_code for c in conflicts]},
    )
    return bs.BlockCreateOut(group_id=group_id, created=created, conflicts=conflicts)
