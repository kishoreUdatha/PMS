"""Work orders -- maintenance jobs.

Housekeeping tasks answer "is this room clean"; a work order answers "what is
broken, who is fixing it and by when". They are kept apart because they have
different people, different urgency and a different end: a task ends when a
room is sellable again, a work order when the fault is fixed -- which may be a
pump or a lift nowhere near a room.

**Timestamps follow the status and are never typed in.** Moving a job to In
progress stamps when work started; Completed stamps when it finished, and
reopening clears that again -- so the days-open figure on the Work Order List
report is measured, not remembered.

**Who can be assigned is who can log in.** There is no staff table yet
(SCR-021), so the assignee list is the organisation's active users, the same
people the rest of the system already knows by name.

Guarded by the ``housekeeping`` permissions: the department that already owns
the state of rooms owns what is broken in them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import (
    _GRANT_SQL, Caller, assert_property_in_org, build_authz,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

workorder_router = APIRouter(
    prefix="/work-orders", tags=["work-orders"], route_class=TransactionalRoute
)

CATEGORIES = {
    "electrical": "Electrical", "plumbing": "Plumbing",
    "hvac": "Air conditioning", "carpentry": "Carpentry",
    "furniture": "Furniture & fixtures", "appliance": "Appliances",
    "civil": "Civil & painting", "it": "IT & telecom", "other": "Other",
}
PRIORITIES = {"low": "Low", "medium": "Medium", "high": "High", "urgent": "Urgent"}
STATUSES = {
    "open": "Open", "in_progress": "In progress", "on_hold": "On hold",
    "completed": "Completed", "cancelled": "Cancelled",
}
#: Still somebody's job.
OPEN = ("open", "in_progress", "on_hold")


class WorkOrderIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    room_id: uuid.UUID | None = None
    location: str | None = Field(None, max_length=120)
    category: str = "other"
    priority: str = "medium"
    status: str = "open"
    assigned_to: uuid.UUID | None = None
    due_date: date | None = None
    cost: Decimal | None = Field(None, ge=0)
    resolution: str | None = Field(None, max_length=500)


class WorkOrder(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    description: str | None
    room_id: uuid.UUID | None
    room_code: str | None
    location: str | None
    where: str
    category: str
    category_label: str
    priority: str
    priority_label: str
    status: str
    status_label: str
    assigned_to: uuid.UUID | None
    assigned_name: str | None
    due_date: date | None
    overdue: bool
    cost: Decimal | None
    resolution: str | None
    reported_by_name: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class Choice(BaseModel):
    value: str
    label: str


class Person(BaseModel):
    id: uuid.UUID
    name: str


class RoomChoice(BaseModel):
    id: uuid.UUID
    code: str


class WorkOrderList(BaseModel):
    rows: list[WorkOrder]
    total: int
    #: Per status over the whole property, plus ``overdue``, whatever the
    #: filter -- the chips count the work, not the page.
    counts: dict[str, int]
    assignees: list[Person]
    rooms: list[RoomChoice]
    categories: list[Choice]
    priorities: list[Choice]
    statuses: list[Choice]
    can_create: bool
    can_edit: bool


_ROW_SQL = """
    SELECT w.*, rm.code AS room_code,
           au.display_name AS assigned_name,
           rb.display_name AS reported_by_name,
           CAST(timezone(COALESCE(p.timezone, 'Asia/Kolkata'), now()) AS date) AS today
    FROM operations.work_orders w
    JOIN iam.properties p ON p.id = w.property_id
    LEFT JOIN property.rooms rm ON rm.id = w.room_id
    LEFT JOIN iam.users au ON au.id = w.assigned_to
    LEFT JOIN iam.users rb ON rb.id = w.reported_by
"""


def _out(r) -> WorkOrder:
    where = " · ".join(x for x in (
        f"Room {r['room_code']}" if r["room_code"] else None, r["location"]) if x)
    return WorkOrder(
        id=r["id"], number=f"WO-{r['number']}", title=r["title"],
        description=r["description"], room_id=r["room_id"],
        room_code=r["room_code"], location=r["location"], where=where or "—",
        category=r["category"],
        category_label=CATEGORIES.get(r["category"], r["category"]),
        priority=r["priority"],
        priority_label=PRIORITIES.get(r["priority"], r["priority"]),
        status=r["status"], status_label=STATUSES.get(r["status"], r["status"]),
        assigned_to=r["assigned_to"], assigned_name=r["assigned_name"],
        due_date=r["due_date"],
        overdue=bool(r["status"] in OPEN and r["due_date"]
                     and r["due_date"] < r["today"]),
        cost=r["cost"], resolution=r["resolution"],
        reported_by_name=r["reported_by_name"], created_at=r["created_at"],
        started_at=r["started_at"], completed_at=r["completed_at"],
    )


def _get(db: Session, work_order_id: uuid.UUID, property_id: uuid.UUID) -> WorkOrder:
    row = db.execute(
        text(_ROW_SQL + " WHERE w.id = :i AND w.property_id = :p"),
        {"i": work_order_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Work order not found.")
    return _out(row)


def _may(db: Session, caller: Caller, property_id: uuid.UUID, action: str) -> bool:
    """For greying out buttons; the endpoints check for themselves."""
    if caller.is_service:
        return True
    return db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "housekeeping", "act": action,
         "prop": property_id},
    ).first() is not None


def _validate(db: Session, property_id: uuid.UUID, body: WorkOrderIn) -> uuid.UUID:
    """Check the body against this property and return its organisation."""
    body.title = body.title.strip()
    body.location = (body.location or "").strip() or None
    if not body.title:
        raise HTTPException(status_code=422, detail="Describe the issue.")
    for value, allowed, what in ((body.category, CATEGORIES, "category"),
                                 (body.priority, PRIORITIES, "priority"),
                                 (body.status, STATUSES, "status")):
        if value not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown {what}. Use one of: {', '.join(allowed)}.")
    if body.room_id is None and body.location is None:
        raise HTTPException(
            status_code=422,
            detail="Say where the work is: pick a room or describe the location.")

    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar()
    if org is None:
        raise HTTPException(status_code=404, detail="Property not found.")
    if body.room_id and db.execute(
        text("SELECT 1 FROM property.rooms WHERE id = :r AND property_id = :p"),
        {"r": body.room_id, "p": property_id},
    ).first() is None:
        raise HTTPException(status_code=422, detail="That room is not at this property.")
    if body.assigned_to and db.execute(
        text("""
            SELECT 1 FROM iam.memberships m
            JOIN iam.users u ON u.id = m.user_id
            WHERE m.user_id = :u AND m.organization_id = :org
              AND m.status = 'active' AND u.status = 'active'
        """),
        {"u": body.assigned_to, "org": org},
    ).first() is None:
        raise HTTPException(
            status_code=422,
            detail="That person is not an active user of this organisation.")
    return org


@workorder_router.get("", response_model=WorkOrderList)
def list_work_orders(
    property_id: uuid.UUID,
    status: str | None = Query(None, description="A status, or 'active' for all open work"),
    priority: str | None = Query(None),
    q: str | None = Query(None),
    caller: Caller = Depends(require_permission("housekeeping", "view")),
    db: Session = Depends(get_session),
):
    """The property's work orders, most urgent first."""
    assert_property_in_org(db, caller, property_id)
    statuses = list(OPEN) if status == "active" else ([status] if status else None)
    rows = db.execute(
        text(_ROW_SQL + """
            WHERE w.property_id = :p
              AND (CAST(:st AS text[]) IS NULL OR w.status = ANY(CAST(:st AS text[])))
              AND (CAST(:pr AS text) IS NULL OR w.priority = CAST(:pr AS text))
              AND (CAST(:q AS text) IS NULL
                   OR w.title ILIKE '%' || CAST(:q AS text) || '%'
                   OR COALESCE(w.location, '') ILIKE '%' || CAST(:q AS text) || '%'
                   OR COALESCE(rm.code, '') ILIKE '%' || CAST(:q AS text) || '%'
                   OR ('WO-' || w.number) ILIKE '%' || CAST(:q AS text) || '%')
            ORDER BY CASE w.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                                     WHEN 'medium' THEN 2 ELSE 3 END,
                     w.due_date NULLS LAST, w.number DESC
            LIMIT 500
        """),
        {"p": property_id, "st": statuses, "pr": priority or None,
         "q": (q or "").strip() or None},
    ).mappings().all()

    counts = {k: 0 for k in STATUSES}
    for s, n in db.execute(
        text("SELECT status, count(*) FROM operations.work_orders "
             "WHERE property_id = :p GROUP BY 1"),
        {"p": property_id},
    ):
        counts[s] = n
    counts["overdue"] = db.execute(
        text("""
            SELECT count(*) FROM operations.work_orders w
            JOIN iam.properties p ON p.id = w.property_id
            WHERE w.property_id = :p AND w.status IN ('open', 'in_progress', 'on_hold')
              AND w.due_date < CAST(timezone(COALESCE(p.timezone, 'Asia/Kolkata'), now()) AS date)
        """),
        {"p": property_id},
    ).scalar() or 0

    assignees = [
        Person(id=r[0], name=r[1] or "Unnamed user")
        for r in db.execute(
            text("""
                SELECT DISTINCT u.id, u.display_name
                FROM iam.users u
                JOIN iam.memberships m ON m.user_id = u.id AND m.status = 'active'
                WHERE u.status = 'active'
                  AND m.organization_id = (SELECT organization_id FROM iam.properties
                                            WHERE id = :p)
                ORDER BY u.display_name
            """),
            {"p": property_id})
    ]
    rooms = [
        RoomChoice(id=r[0], code=r[1])
        for r in db.execute(
            text("SELECT id, code FROM property.rooms "
                 "WHERE property_id = :p AND status = 'active' ORDER BY code"),
            {"p": property_id})
    ]
    out = [_out(r) for r in rows]
    return WorkOrderList(
        rows=out, total=len(out), counts=counts, assignees=assignees, rooms=rooms,
        categories=[Choice(value=k, label=v) for k, v in CATEGORIES.items()],
        priorities=[Choice(value=k, label=v) for k, v in PRIORITIES.items()],
        statuses=[Choice(value=k, label=v) for k, v in STATUSES.items()],
        can_create=_may(db, caller, property_id, "create"),
        can_edit=_may(db, caller, property_id, "edit"),
    )


@workorder_router.post("", response_model=WorkOrder, status_code=201)
def create_work_order(
    property_id: uuid.UUID,
    body: WorkOrderIn,
    caller: Caller = Depends(require_permission("housekeeping", "create")),
    db: Session = Depends(get_session),
):
    """Report a fault."""
    assert_property_in_org(db, caller, property_id)
    org = _validate(db, property_id, body)
    # One number at a time per property: a transaction-scoped lock rather
    # than max()+1 racing another reporter for the same number.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(CAST(:k AS text)))"),
               {"k": f"work-order:{property_id}"})
    number = db.execute(
        text("SELECT COALESCE(max(number), 1000) + 1 FROM operations.work_orders "
             "WHERE property_id = :p"),
        {"p": property_id},
    ).scalar_one()
    wid = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO operations.work_orders
                (id, organization_id, property_id, number, room_id, location,
                 title, description, category, priority, status, assigned_to,
                 due_date, cost, resolution, reported_by, updated_by,
                 started_at, completed_at)
            VALUES (:id, :org, :p, :n, :room_id, :location, :title, :description,
                    :category, :priority, :status, :assigned_to, :due_date, :cost,
                    :resolution, :who, :who,
                    CASE WHEN CAST(:status AS varchar) IN ('in_progress', 'completed')
                         THEN now() END,
                    CASE WHEN CAST(:status AS varchar) = 'completed' THEN now() END)
        """),
        {"id": wid, "org": org, "p": property_id, "n": number,
         "who": caller.user_id, **body.model_dump()},
    )
    record_audit(
        db, action="work_order.created", entity_type="work_order",
        entity_id=str(wid), organization_id=org, property_id=property_id,
        actor_subject=caller.subject,
        after={"number": f"WO-{number}", "title": body.title,
               "priority": body.priority},
    )
    return _get(db, wid, property_id)


@workorder_router.patch("/{work_order_id}", response_model=WorkOrder)
def update_work_order(
    work_order_id: uuid.UUID,
    property_id: uuid.UUID,
    body: WorkOrderIn,
    caller: Caller = Depends(require_permission("housekeeping", "edit")),
    db: Session = Depends(get_session),
):
    """Assign, reprioritise, progress or close a job."""
    assert_property_in_org(db, caller, property_id)
    before = db.execute(
        text("SELECT * FROM operations.work_orders "
             "WHERE id = :i AND property_id = :p FOR UPDATE"),
        {"i": work_order_id, "p": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Work order not found.")
    org = _validate(db, property_id, body)
    db.execute(
        text("""
            UPDATE operations.work_orders SET
                room_id = :room_id, location = :location, title = :title,
                description = :description, category = :category,
                priority = :priority, status = :status,
                assigned_to = :assigned_to, due_date = :due_date, cost = :cost,
                resolution = :resolution, updated_by = :who,
                started_at = CASE
                    WHEN CAST(:status AS varchar) IN ('in_progress', 'completed')
                    THEN COALESCE(started_at, now())
                    ELSE started_at END,
                completed_at = CASE
                    WHEN CAST(:status AS varchar) = 'completed' THEN COALESCE(completed_at, now())
                    WHEN CAST(:status AS varchar) IN ('open', 'in_progress', 'on_hold') THEN NULL
                    ELSE completed_at END,
                updated_at = now(), version = version + 1
            WHERE id = :id
        """),
        {"id": work_order_id, "who": caller.user_id, **body.model_dump()},
    )
    watched = ("status", "priority", "assigned_to", "due_date")
    changed = {k: str(getattr(body, k)) for k in watched
               if str(getattr(body, k) or "") != str(before[k] or "")}
    if changed:
        record_audit(
            db, action="work_order.updated", entity_type="work_order",
            entity_id=str(work_order_id), organization_id=org,
            property_id=property_id, actor_subject=caller.subject,
            before={k: str(before[k]) for k in changed},
            after=changed,
        )
    return _get(db, work_order_id, property_id)
