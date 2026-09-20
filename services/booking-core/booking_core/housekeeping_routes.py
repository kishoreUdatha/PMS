"""Housekeeping operations — the room board (screen 006).

Checkout has been marking rooms dirty for a while, and until now that was the
end of the story: the condition was recorded and nothing consumed it. This is
the other half. A dirty room becomes a task, the task gets an attendant, the
attendant works it, someone inspects it, and the room comes back sellable.

Two rules shape everything here.

**Room condition remains the single source of truth for what a room is.** The
task drives ``operations.room_condition`` at each transition rather than
carrying a second opinion about cleanliness. The rack, the dashboard and the
room history read that same column, so they cannot drift out of step with this
board — and every transition also lands on the room's status timeline, because
"who made this room ready, and when" is a question people actually ask.

**The five lanes are one state machine plus a gap.** Assigned, In Progress,
Inspection and Ready are literal task states. Dirty is the gap: rooms the system
knows are dirty with no open task against them — the pile nobody has picked up,
which is the whole reason to look at this screen in the morning.

Attendants are not a staff table; SCR-021 has not been built. They are the
active users who actually hold ``housekeeping.edit`` in this property, which is
both true today and the right answer later — a person who cannot be granted the
permission should not be handed a room.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .history_routes import record_status_event
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

housekeeping_router = APIRouter(
    prefix="/housekeeping", tags=["housekeeping"], route_class=TransactionalRoute
)

# Lane -> the task state behind it. "dirty" has no state: it is the rooms with
# no open task at all.
LANES = ("dirty", "assigned", "in_progress", "inspection", "ready")
LANE_LABELS = {
    "dirty": "Dirty", "assigned": "Assigned", "in_progress": "In Progress",
    "inspection": "Inspection", "ready": "Ready",
}
PRIORITIES = ("urgent", "high", "normal", "low")
KINDS = ("departure", "stayover", "turndown", "deep_clean", "inspection_only")

# Task state -> the room condition it implies. This is the only place the two
# vocabularies meet.
CONDITION_FOR = {
    "assigned": "dirty",
    "in_progress": "cleaning",
    "inspection": "clean",
    "ready": "inspected",
}

# A guest arriving within this many minutes makes a room urgent to turn around.
IMMINENT_MINUTES = 90


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class Attendant(BaseModel):
    user_id: uuid.UUID
    name: str
    open_tasks: int
    done_today: int
    total_today: int
    percent_done: int


class TaskCard(BaseModel):
    room_id: uuid.UUID
    room_code: str
    room_type: str
    floor: str | None
    lane: str
    condition: str
    priority: str
    task_id: uuid.UUID | None
    kind: str | None
    attendant_id: uuid.UUID | None
    attendant: str | None
    started_at: datetime | None
    elapsed_minutes: int | None
    finished_at: datetime | None
    rejected_count: int
    notes: str | None
    # Departure and arrival context, which is what makes one dirty room more
    # urgent than another.
    departure_note: str | None
    arrival_note: str | None
    arriving_in_minutes: int | None
    occupied: bool


class Lane(BaseModel):
    key: str
    label: str
    count: int
    cards: list[TaskCard]


class BoardKpis(BaseModel):
    dirty: int
    cleaning: int
    ready: int
    inspection_due: int
    staff_on_duty: int


class RoomTypeOption(BaseModel):
    id: uuid.UUID
    name: str


class FilterOptions(BaseModel):
    floors: list[str]
    room_types: list[RoomTypeOption]
    attendants: list[Attendant]


class Board(BaseModel):
    business_date: date
    checkin_time: str | None
    checkout_time: str | None
    kpis: BoardKpis
    lanes: list[Lane]
    filters: FilterOptions
    can_edit: bool
    can_create: bool
    can_approve: bool
    # Whether this caller may take the board away as a file. The attendants'
    # run sheet is printed and carried, so it is a real permission.
    can_export: bool = False
    unassigned: int


class ActivityRow(BaseModel):
    at: datetime
    room_code: str
    text: str
    tone: str


class TaskIn(BaseModel):
    room_id: uuid.UUID
    kind: str = "departure"
    priority: str = "normal"
    assigned_to: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=600)


class TaskOut(BaseModel):
    id: uuid.UUID
    room_code: str
    attendant: str | None = None
    state: str | None = None
    condition: str | None = None


class AssignIn(BaseModel):
    assigned_to: uuid.UUID | None = None
    priority: str | None = None
    notes: str | None = Field(default=None, max_length=600)


class AdvanceIn(BaseModel):
    action: str = "start"  # start | finish | pass | fail
    notes: str | None = Field(default=None, max_length=600)


class AutoAssignOut(BaseModel):
    assigned: int
    skipped: int
    detail: str


# --------------------------------------------------------------------------
# Shared reads
# --------------------------------------------------------------------------
def _property(db: Session, property_id: uuid.UUID):
    row = db.execute(
        text("SELECT id, organization_id, name, checkin_time, checkout_time "
             "FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return row


def _may(db: Session, caller: Caller, property_id: uuid.UUID, action: str) -> bool:
    """Whether the caller holds one more housekeeping action than ``view``.

    Used to grey out buttons, not to guard anything — the endpoints behind
    those buttons ask for the permission themselves.
    """
    from chirala_common.authz import _GRANT_SQL  # the one canonical query

    return db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "housekeeping", "act": action,
         "prop": property_id},
    ).first() is not None


def _attendants(db: Session, property_id: uuid.UUID, org_id: uuid.UUID,
                on_date: date) -> list[Attendant]:
    """Active users who hold ``housekeeping.edit`` here, with today's load."""
    rows = db.execute(
        text(
            """
            SELECT u.id, u.display_name,
                   count(t.id) FILTER (
                       WHERE t.state IN ('assigned', 'in_progress', 'inspection')
                   ) AS open_tasks,
                   count(t.id) FILTER (WHERE t.state = 'ready')  AS done_today,
                   count(t.id)                                   AS total_today
            FROM iam.users u
            JOIN iam.memberships m
              ON m.user_id = u.id AND m.status = 'active'
             AND m.organization_id = :org
            JOIN iam.role_assignments ra ON ra.membership_id = m.id
            JOIN iam.role_permissions rp ON rp.role_id = ra.role_id
            JOIN iam.permissions p
              ON p.id = rp.permission_id
             AND p.resource_code = 'housekeeping'
             AND p.action_code = 'edit'
            LEFT JOIN operations.housekeeping_tasks t
              ON t.assigned_to = u.id
             AND t.property_id = :prop
             AND t.task_date = :d
             AND t.state <> 'cancelled'
            WHERE u.status = 'active'
              AND (ra.scope_type = 'organization' OR ra.property_id = :prop)
            GROUP BY u.id, u.display_name
            ORDER BY u.display_name
            """
        ),
        {"org": org_id, "prop": property_id, "d": on_date},
    ).mappings().all()
    return [
        Attendant(
            user_id=r["id"], name=r["display_name"],
            open_tasks=r["open_tasks"], done_today=r["done_today"],
            total_today=r["total_today"],
            percent_done=round(100 * r["done_today"] / r["total_today"])
            if r["total_today"] else 0,
        )
        for r in rows
    ]


# The board's room-level facts, one row per active room: its condition, its
# open task if it has one, when it was vacated and who is due into it next.
_BOARD_SQL = """
    WITH open_task AS (
        SELECT DISTINCT ON (room_id) *
        FROM operations.housekeeping_tasks
        WHERE property_id = :prop
          AND state IN ('assigned', 'in_progress', 'inspection')
        ORDER BY room_id, created_at DESC
    ),
    done_today AS (
        SELECT DISTINCT ON (room_id) *
        FROM operations.housekeeping_tasks
        WHERE property_id = :prop
          AND state = 'ready'
          AND task_date = :d
        ORDER BY room_id, finished_at DESC NULLS LAST
    ),
    departed AS (
        -- The most recent checkout from this room today, for "vacated at".
        SELECT DISTINCT ON (co.room_id) co.room_id, co.checked_out_at
        FROM booking.stay_checkouts co
        WHERE co.property_id = :prop
          AND co.checked_out_at >= CAST(:d AS date)
        ORDER BY co.room_id, co.checked_out_at DESC
    ),
    occupancy AS (
        -- Whoever holds the room right now, and whoever holds it next.
        -- The period is a tstzrange; the rest of this screen works in whole
        -- days, so it is reduced to dates here rather than at every comparison.
        SELECT e.room_id,
               bool_or(lower(e.occupied_period) <= CAST(:d AS timestamptz)
                       AND upper(e.occupied_period) > CAST(:d AS timestamptz))
                   AS occupied_now,
               min(CAST(lower(e.occupied_period) AS date))
                   FILTER (WHERE lower(e.occupied_period) >= CAST(:d AS timestamptz))
                   AS next_arrival
        FROM booking.room_calendar_entries e
        WHERE e.property_id = :prop
          AND e.status <> 'released'
          AND upper(e.occupied_period) >= CAST(:d AS timestamptz)
        GROUP BY e.room_id
    )
    SELECT rm.id AS room_id, rm.code AS room_code, rm.floor,
           rt.id AS room_type_id, rt.name AS room_type,
           COALESCE(rc.cleanliness, 'clean') AS condition,
           t.id AS task_id, t.state, t.priority, t.kind, t.notes,
           t.assigned_to, t.started_at, t.finished_at, t.rejected_count,
           u.display_name AS attendant,
           dt.id AS done_task_id, dt.assigned_to AS done_by,
           du.display_name AS done_attendant, dt.finished_at AS done_at,
           dep.checked_out_at,
           oc.occupied_now, oc.next_arrival
    FROM property.rooms rm
    JOIN property.room_types rt ON rt.id = rm.room_type_id
    LEFT JOIN operations.room_condition rc ON rc.room_id = rm.id
    LEFT JOIN open_task t ON t.room_id = rm.id
    LEFT JOIN done_today dt ON dt.room_id = rm.id
    LEFT JOIN iam.users u ON u.id = t.assigned_to
    LEFT JOIN iam.users du ON du.id = dt.assigned_to
    LEFT JOIN departed dep ON dep.room_id = rm.id
    LEFT JOIN occupancy oc ON oc.room_id = rm.id
    WHERE rm.property_id = :prop
      AND rm.status = 'active'
    ORDER BY rm.floor NULLS LAST, rm.code
"""


def _parse_time(v) -> time | None:
    """The property's check-in/out times are stored as "HH:MM" strings."""
    if v is None:
        return None
    if isinstance(v, time):
        return v
    try:
        hh, _, mm = str(v).partition(":")
        return time(int(hh), int(mm or 0))
    except ValueError:
        return None


def _fmt_time(v) -> str | None:
    """24-hour, like every other time the product shows.

    This was the one place a time reached a screen already formatted, and it
    formatted it as "11:00 AM" while the frontend wrote every other time of
    day as 11:00 -- so the housekeeping header disagreed with the check-in
    screen about what the same standard checkout time looked like.
    """
    t = _parse_time(v)
    if t is None:
        return None
    return f"{t.hour:02d}:{t.minute:02d}"


# --------------------------------------------------------------------------
# The board
# --------------------------------------------------------------------------
@housekeeping_router.get("/board", response_model=Board)
def board(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    floor: str | None = Query(None),
    room_type_id: uuid.UUID | None = Query(None),
    lane: str | None = Query(None),
    attendant_id: uuid.UUID | None = Query(None),
    caller: Caller = Depends(require_permission("housekeeping", "view")),
    db: Session = Depends(get_session),
) -> Board:
    """Every active room, sorted into the lane it is actually in."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    today = on_date or db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    now = db.execute(text("SELECT now()")).scalar_one()

    rows = db.execute(
        text(_BOARD_SQL), {"prop": property_id, "d": today}
    ).mappings().all()

    checkin_at = None
    standard_checkin = _parse_time(prop["checkin_time"])
    if standard_checkin is not None:
        checkin_at = datetime.combine(today, standard_checkin, tzinfo=now.tzinfo)

    cards: list[TaskCard] = []
    for r in rows:
        # Lane: the open task's state, else "ready" for a room finished today,
        # else the condition decides.
        if r["state"]:
            lane_key = r["state"]
        elif r["condition"] in ("dirty", "cleaning"):
            # Condition wins over a task finished earlier today: a room cleaned
            # this morning and dirtied again this afternoon is dirty, and
            # letting the finished task speak for it would hide exactly the
            # rooms this board exists to surface.
            lane_key = "dirty"
        elif r["done_task_id"] or r["condition"] == "inspected":
            lane_key = "ready"
        else:
            # Clean but not inspected today: still sellable, so it belongs with
            # the ready rooms rather than in a lane of its own.
            lane_key = "ready"

        arriving_in = None
        arrival_note = None
        if r["next_arrival"] == today and not r["occupied_now"]:
            if checkin_at is not None:
                arriving_in = int((checkin_at - now).total_seconds() // 60)
                arrival_note = f"Guest arriving {_fmt_time(prop['checkin_time'])}"
            else:
                arrival_note = "Guest arriving today"
        elif r["next_arrival"] and r["next_arrival"] > today:
            days = (r["next_arrival"] - today).days
            arrival_note = ("Next arrival tomorrow" if days == 1
                            else f"Next arrival in {days} days")
        elif r["occupied_now"]:
            arrival_note = "Guest in house"

        if r["checked_out_at"]:
            departure_note = f"Vacated {_fmt_time(r['checked_out_at'].time())}"
        elif r["occupied_now"]:
            departure_note = (f"Checkout by {_fmt_time(prop['checkout_time'])}"
                              if prop["checkout_time"] else "Occupied")
        else:
            departure_note = None

        # Priority: the task's own, or — for an untouched dirty room — derived
        # from how soon somebody needs it.
        if r["priority"]:
            priority = r["priority"]
        elif arriving_in is not None and arriving_in <= IMMINENT_MINUTES:
            priority = "urgent"
        elif r["next_arrival"] == today:
            priority = "high"
        else:
            priority = "normal"

        started = r["started_at"]
        cards.append(TaskCard(
            room_id=r["room_id"], room_code=r["room_code"],
            room_type=r["room_type"], floor=r["floor"],
            lane=lane_key, condition=r["condition"], priority=priority,
            task_id=r["task_id"] or r["done_task_id"],
            kind=r["kind"],
            attendant_id=r["assigned_to"] or r["done_by"],
            attendant=r["attendant"] or r["done_attendant"],
            started_at=started,
            elapsed_minutes=int((now - started).total_seconds() // 60)
            if started and r["state"] == "in_progress" else None,
            finished_at=r["finished_at"] or r["done_at"],
            rejected_count=r["rejected_count"] or 0,
            notes=r["notes"],
            departure_note=departure_note, arrival_note=arrival_note,
            arriving_in_minutes=arriving_in,
            occupied=bool(r["occupied_now"]),
        ))

    # KPIs are counted before filtering: a filtered board still reports the true
    # state of the property, otherwise the headline numbers move when you search.
    people = _attendants(db, property_id, prop["organization_id"], today)
    kpis = BoardKpis(
        dirty=sum(1 for c in cards if c.lane == "dirty"),
        cleaning=sum(1 for c in cards if c.lane == "in_progress"),
        ready=sum(1 for c in cards if c.lane == "ready"),
        inspection_due=sum(1 for c in cards if c.lane == "inspection"),
        staff_on_duty=sum(1 for a in people if a.total_today),
    )
    unassigned = kpis.dirty

    keep = [
        c for c in cards
        if (floor is None or c.floor == floor)
        and (lane is None or c.lane == lane)
        and (attendant_id is None or c.attendant_id == attendant_id)
    ]
    if room_type_id is not None:
        names = {r["room_type"] for r in rows if r["room_type_id"] == room_type_id}
        keep = [c for c in keep if c.room_type in names]

    order = {p: i for i, p in enumerate(PRIORITIES)}
    lanes = []
    for key in LANES:
        in_lane = sorted(
            (c for c in keep if c.lane == key),
            key=lambda c: (order.get(c.priority, 9), c.room_code),
        )
        lanes.append(Lane(key=key, label=LANE_LABELS[key],
                          count=len(in_lane), cards=in_lane))

    floors = sorted({r["floor"] for r in rows if r["floor"]})
    types = sorted(
        {(r["room_type_id"], r["room_type"]) for r in rows}, key=lambda t: t[1]
    )
    return Board(
        business_date=today,
        checkin_time=_fmt_time(prop["checkin_time"]),
        checkout_time=_fmt_time(prop["checkout_time"]),
        kpis=kpis, lanes=lanes,
        filters=FilterOptions(
            floors=floors,
            room_types=[RoomTypeOption(id=i, name=n) for i, n in types],
            attendants=people,
        ),
        can_edit=_may(db, caller, property_id, "edit"),
        can_create=_may(db, caller, property_id, "create"),
        can_export=_may(db, caller, property_id, "export"),
        can_approve=_may(db, caller, property_id, "approve"),
        unassigned=unassigned,
    )


@housekeeping_router.get("/activity", response_model=list[ActivityRow])
def activity(
    property_id: uuid.UUID,
    limit: int = Query(12, ge=1, le=100),
    caller: Caller = Depends(require_permission("housekeeping", "view")),
    db: Session = Depends(get_session),
):
    """What has happened to rooms lately, newest first.

    Read from the room status timeline rather than from the task table, so
    changes made elsewhere — a checkout, a maintenance block — show up here too
    instead of the board pretending it is the only thing that touches rooms.
    """
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT e.occurred_at, e.status, e.source, e.remarks,
                   rm.code AS room_code, u.display_name AS who
            FROM operations.room_status_events e
            JOIN property.rooms rm ON rm.id = e.room_id
            LEFT JOIN iam.users u ON u.id = e.changed_by
            WHERE e.property_id = :prop
            ORDER BY e.occurred_at DESC
            LIMIT :lim
            """
        ),
        {"prop": property_id, "lim": limit},
    ).mappings().all()
    tone = {"dirty": "rose", "cleaning": "amber", "clean": "sky",
            "inspected": "emerald"}
    labels = {
        "dirty": "marked Dirty", "cleaning": "cleaning started",
        "clean": "cleaning finished", "inspected": "passed inspection",
        "occupied": "occupied", "available": "released",
        "out_of_order": "put out of order", "blocked": "blocked",
        "maintenance": "sent to maintenance",
    }
    return [
        ActivityRow(
            at=r["occurred_at"], room_code=r["room_code"],
            text=(f"Room {r['room_code']} "
                  f"{labels.get(r['status'], 'set to ' + r['status'])}"
                  f"{' by ' + r['who'] if r['who'] else ''}"),
            tone=tone.get(r["status"], "slate"),
        )
        for r in rows
    ]


# --------------------------------------------------------------------------
# Changing a task
# --------------------------------------------------------------------------
def _sync_condition(db: Session, caller: Caller, *, org_id, property_id,
                    room_id, state: str, remarks: str | None) -> None:
    """Move the room's condition to match the task, and say so on the timeline.

    The condition column is what the rest of the product reads; letting the task
    carry its own idea of cleanliness would give the rack and this board two
    different answers to the same question.
    """
    condition = CONDITION_FOR[state]
    db.execute(
        text(
            """
            INSERT INTO operations.room_condition
                (room_id, organization_id, property_id, cleanliness, updated_at)
            VALUES (:id, :org, :prop, :c, now())
            ON CONFLICT (room_id) DO UPDATE
                SET cleanliness = EXCLUDED.cleanliness,
                    updated_at = now(),
                    version = operations.room_condition.version + 1
            """
        ),
        {"id": room_id, "org": org_id, "prop": property_id, "c": condition},
    )
    if condition == "inspected":
        db.execute(
            text("UPDATE operations.room_condition SET inspected_at = now(), "
                 "inspector_id = :who WHERE room_id = :id"),
            {"who": caller.user_id, "id": room_id},
        )
    record_status_event(
        db, organization_id=org_id, property_id=property_id, room_id=room_id,
        status=condition, source="housekeeping", remarks=remarks,
        changed_by=caller.user_id,
    )


def _task(db: Session, task_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text(
            """
            SELECT t.*, rm.code AS room_code
            FROM operations.housekeeping_tasks t
            JOIN property.rooms rm ON rm.id = t.room_id
            WHERE t.id = :id AND t.property_id = :prop
            """
        ),
        {"id": task_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return row


def _check_attendant(db: Session, property_id, org_id, on_date,
                     user_id: uuid.UUID) -> str:
    for a in _attendants(db, property_id, org_id, on_date):
        if a.user_id == user_id:
            return a.name
    raise HTTPException(
        status_code=422,
        detail="That person cannot be given housekeeping work — they need the "
               "housekeeping edit permission in this property first.",
    )


@housekeeping_router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(
    property_id: uuid.UUID,
    body: TaskIn,
    caller: Caller = Depends(require_permission("housekeeping", "create")),
    db: Session = Depends(get_session),
) -> TaskOut:
    """Raise a piece of work against a room."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    if body.kind not in KINDS:
        raise HTTPException(status_code=422, detail="Unknown task type.")
    if body.priority not in PRIORITIES:
        raise HTTPException(status_code=422, detail="Unknown priority.")

    room = db.execute(
        text("SELECT id, code FROM property.rooms "
             "WHERE id = :id AND property_id = :prop AND status = 'active'"),
        {"id": body.room_id, "prop": property_id},
    ).mappings().first()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    open_now = db.execute(
        text("SELECT id, state FROM operations.housekeeping_tasks WHERE room_id = :r "
             "AND state IN ('assigned', 'in_progress', 'inspection')"),
        {"r": body.room_id},
    ).mappings().first()
    if open_now is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Room {room['code']} already has an open task "
                   f"({LANE_LABELS.get(open_now['state'], open_now['state'])}).",
        )

    name = None
    if body.assigned_to is not None:
        name = _check_attendant(db, property_id, prop["organization_id"],
                                today, body.assigned_to)

    task_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO operations.housekeeping_tasks
                (id, organization_id, property_id, room_id, task_date, kind,
                 priority, state, assigned_to, assigned_at, notes, created_by)
            VALUES (:id, :org, :prop, :room, :d, :kind, :pri, 'assigned',
                    CAST(:who AS uuid),
                    CASE WHEN CAST(:who AS uuid) IS NULL THEN NULL ELSE now() END,
                    :notes, :by)
            """
        ),
        {"id": task_id, "org": prop["organization_id"], "prop": property_id,
         "room": body.room_id, "d": today, "kind": body.kind,
         "pri": body.priority, "who": body.assigned_to, "notes": body.notes,
         "by": caller.user_id},
    )
    _sync_condition(db, caller, org_id=prop["organization_id"],
                    property_id=property_id, room_id=body.room_id,
                    state="assigned", remarks=body.notes)
    record_audit(
        db, action="housekeeping.task.created", entity_type="room",
        entity_id=str(body.room_id), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"task_id": str(task_id), "room": room["code"], "kind": body.kind,
               "priority": body.priority, "assigned_to": name},
    )
    return TaskOut(id=task_id, room_code=room["code"], attendant=name,
                   state="assigned", condition="dirty")


@housekeeping_router.post("/tasks/{task_id}/assign", response_model=TaskOut)
def assign_task(
    task_id: uuid.UUID,
    property_id: uuid.UUID,
    body: AssignIn,
    caller: Caller = Depends(require_permission("housekeeping", "edit")),
    db: Session = Depends(get_session),
):
    """Hand the task to somebody, or change its priority."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    row = _task(db, task_id, property_id)
    if row["state"] in ("ready", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Room {row['room_code']} is already "
                   f"{'finished' if row['state'] == 'ready' else 'cancelled'}.",
        )
    if body.priority is not None and body.priority not in PRIORITIES:
        raise HTTPException(status_code=422, detail="Unknown priority.")

    name = None
    if body.assigned_to is not None:
        name = _check_attendant(db, property_id, prop["organization_id"],
                                row["task_date"], body.assigned_to)
    db.execute(
        text(
            """
            UPDATE operations.housekeeping_tasks
               SET assigned_to = CASE WHEN :set_who
                                      THEN CAST(:who AS uuid) ELSE assigned_to END,
                   assigned_at = CASE WHEN :set_who THEN now() ELSE assigned_at END,
                   priority    = COALESCE(CAST(:pri AS varchar), priority),
                   notes       = COALESCE(CAST(:notes AS varchar), notes),
                   updated_at  = now(),
                   version     = version + 1
             WHERE id = :id
            """
        ),
        {"id": task_id, "set_who": body.assigned_to is not None,
         "who": body.assigned_to, "pri": body.priority, "notes": body.notes},
    )
    record_audit(
        db, action="housekeeping.task.assigned", entity_type="room",
        entity_id=str(row["room_id"]), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        before={"assigned_to": str(row["assigned_to"]) if row["assigned_to"] else None,
                "priority": row["priority"]},
        after={"assigned_to": name, "priority": body.priority or row["priority"]},
    )
    return TaskOut(id=task_id, room_code=row["room_code"], attendant=name,
                   state=row["state"])


_NEXT = {
    ("assigned", "start"): "in_progress",
    ("in_progress", "finish"): "inspection",
    ("inspection", "pass"): "ready",
    ("inspection", "fail"): "in_progress",
}


@housekeeping_router.post("/tasks/{task_id}/advance", response_model=TaskOut)
def advance_task(
    task_id: uuid.UUID,
    property_id: uuid.UUID,
    body: AdvanceIn,
    caller: Caller = Depends(require_permission("housekeeping", "edit")),
    db: Session = Depends(get_session),
):
    """Move the task one step, and the room's condition with it."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    row = _task(db, task_id, property_id)

    nxt = _NEXT.get((row["state"], body.action))
    if nxt is None:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot {body.action} a task that is "
                   f"{LANE_LABELS.get(row['state'], row['state'])}.",
        )
    # Signing off an inspection is a different permission from doing the work.
    if body.action in ("pass", "fail") and not _may(db, caller, property_id,
                                                    "approve"):
        raise HTTPException(
            status_code=403,
            detail="Signing off an inspection needs the housekeeping approve "
                   "permission.",
        )
    if row["assigned_to"] is None and body.action == "start":
        raise HTTPException(
            status_code=422,
            detail=f"Assign room {row['room_code']} to somebody before starting it.",
        )

    db.execute(
        text(
            """
            -- :s is both written into a varchar column and compared against
            -- text literals below; without the cast Postgres deduces two
            -- different types for the one parameter and refuses the statement.
            UPDATE operations.housekeeping_tasks
               SET state = CAST(:s AS varchar),
                   started_at   = CASE WHEN CAST(:s AS varchar) = 'in_progress'
                                        AND started_at IS NULL
                                       THEN now() ELSE started_at END,
                   finished_at  = CASE WHEN CAST(:s AS varchar) = 'inspection'
                                       THEN now()
                                       WHEN CAST(:s AS varchar) = 'in_progress'
                                       THEN NULL
                                       ELSE finished_at END,
                   inspected_by = CASE WHEN CAST(:s AS varchar) = 'ready'
                                       THEN CAST(:who AS uuid)
                                       ELSE inspected_by END,
                   inspected_at = CASE WHEN CAST(:s AS varchar) = 'ready'
                                       THEN now() ELSE inspected_at END,
                   rejected_count = rejected_count
                                    + CASE WHEN :failed THEN 1 ELSE 0 END,
                   notes = COALESCE(CAST(:notes AS varchar), notes),
                   updated_at = now(),
                   version = version + 1
             WHERE id = :id
            """
        ),
        {"id": task_id, "s": nxt, "who": caller.user_id,
         "failed": body.action == "fail", "notes": body.notes},
    )
    _sync_condition(db, caller, org_id=prop["organization_id"],
                    property_id=property_id, room_id=row["room_id"],
                    state=nxt, remarks=body.notes)
    record_audit(
        db, action=f"housekeeping.task.{body.action}", entity_type="room",
        entity_id=str(row["room_id"]), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.notes, before={"state": row["state"]},
        after={"state": nxt, "room": row["room_code"],
               "condition": CONDITION_FOR[nxt]},
    )
    return TaskOut(id=task_id, room_code=row["room_code"], state=nxt,
                   condition=CONDITION_FOR[nxt])


@housekeeping_router.post("/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(
    task_id: uuid.UUID,
    property_id: uuid.UUID,
    body: AdvanceIn,
    caller: Caller = Depends(require_permission("housekeeping", "cancel")),
    db: Session = Depends(get_session),
):
    """Drop the task. The room keeps whatever condition it already had."""
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    row = _task(db, task_id, property_id)
    if row["state"] in ("ready", "cancelled"):
        raise HTTPException(status_code=409, detail="That task is already closed.")
    db.execute(
        text("UPDATE operations.housekeeping_tasks SET state = 'cancelled', "
             "notes = COALESCE(CAST(:n AS varchar), notes), updated_at = now(), "
             "version = version + 1 WHERE id = :id"),
        {"id": task_id, "n": body.notes},
    )
    record_audit(
        db, action="housekeeping.task.cancelled", entity_type="room",
        entity_id=str(row["room_id"]), organization_id=prop["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.notes, before={"state": row["state"]},
        after={"state": "cancelled", "room": row["room_code"]},
    )
    return TaskOut(id=task_id, room_code=row["room_code"], state="cancelled")


@housekeeping_router.post("/auto-assign", response_model=AutoAssignOut)
def auto_assign(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("housekeeping", "edit")),
    db: Session = Depends(get_session),
):
    """Spread the untouched dirty rooms across the attendants.

    Urgent rooms go out first, and each one lands on whoever currently holds the
    least work — counting what is handed out in this same pass, so the result is
    level rather than merely round-robin. Nothing already assigned is moved: an
    attendant halfway through a room should not have it taken away.
    """
    assert_property_in_org(db, caller, property_id)
    prop = _property(db, property_id)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()

    people = _attendants(db, property_id, prop["organization_id"], today)
    if not people:
        raise HTTPException(
            status_code=422,
            detail="Nobody in this property holds the housekeeping edit "
                   "permission, so there is no one to assign rooms to.",
        )

    view = board(property_id=property_id, on_date=today, floor=None,
                 room_type_id=None, lane="dirty", attendant_id=None,
                 caller=caller, db=db)
    todo = list(view.lanes[0].cards)
    if not todo:
        return AutoAssignOut(
            assigned=0, skipped=0,
            detail="Nothing waiting — every dirty room already has someone on it.",
        )

    load = {p.user_id: p.open_tasks for p in people}
    order = {p: i for i, p in enumerate(PRIORITIES)}
    todo.sort(key=lambda c: (order.get(c.priority, 9), c.room_code))

    assigned = skipped = 0
    for card in todo:
        who = min(load, key=lambda u: (load[u], str(u)))
        try:
            create_task(
                property_id=property_id,
                body=TaskIn(room_id=card.room_id, kind="departure",
                            priority=card.priority, assigned_to=who,
                            notes="Auto-assigned"),
                caller=caller, db=db,
            )
        except HTTPException:
            # A room that gained a task between the read and the write is not an
            # error; it is simply already handled.
            skipped += 1
            continue
        load[who] += 1
        assigned += 1

    names = ", ".join(p.name for p in people)
    return AutoAssignOut(
        assigned=assigned, skipped=skipped,
        detail=f"{assigned} room{'' if assigned == 1 else 's'} spread across "
               f"{len(people)} attendant{'' if len(people) == 1 else 's'}: {names}.",
    )
