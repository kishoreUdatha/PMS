"""Rate Rules API (screen 119).

A rate rule is a standing instruction that adjusts a rate automatically, rather
than someone typing a number. Rules layer: several can touch the same night, and
``priority`` decides the order, lowest first.

Two pieces here earn their keep beyond CRUD.

**Conflicts** are computed, not asserted. Two *published* rules that share a
priority, overlap in dates and weekdays, and can both hit the same room type and
rate plan will fight over the same night — so the screen can show that before
someone finds it in a price.

**Simulate** runs the same resolution the rest of the system would: the room
type's effective nightly rate, the rate plan's own pricing, then each applicable
rule in priority order. It reads the real calendar and the real plans, so what
it previews is what the configuration actually produces.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import rate_publish
from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

rule_router = APIRouter(tags=["rate-rules"], route_class=TransactionalRoute)

RULE_TYPES = ("derived_adjustment", "fixed_rate")
STATUSES = ("draft", "scheduled", "published", "paused", "inactive")
APPLICABLE_FOR = ("all_rate_plans", "specific_rate_plans")
DIRECTIONS = ("increase", "decrease")
ADJUSTMENT_TYPES = ("percent", "amount")

RULE_TYPE_LABELS = {
    "derived_adjustment": "Derived Rate Adjustment",
    "fixed_rate": "Fixed Rate Override",
}
STATUS_LABELS = {
    "draft": "Draft", "scheduled": "Scheduled", "published": "Published",
    "paused": "Paused", "inactive": "Inactive",
}
DAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
WEEKDAY_SETS = {
    (1, 2, 3, 4, 5): "Weekdays (Mon-Fri)",
    (6, 7): "Weekends (Sat-Sun)",
    (5, 6): "Weekends (Fri-Sat)",
}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    rule_type: str = "derived_adjustment"
    status: str = "draft"
    priority: int = 10
    applicable_for: str = "all_rate_plans"
    date_from: date
    date_to: date
    weekdays: list[int] = Field(default_factory=list)
    adjustment_direction: str = "decrease"
    adjustment_type: str = "percent"
    adjustment_value: Decimal = Decimal("0")
    fixed_rate: Decimal | None = None
    min_stay: int | None = None
    max_stay: int | None = None
    advance_days_min: int | None = None
    advance_days_max: int | None = None
    #: Apply only while the date is this full, as a percent of sellable rooms.
    #: ``occupancy_min`` alone is the surcharge on a filling date;
    #: ``occupancy_max`` alone is the discount on an empty one.
    occupancy_min: int | None = Field(default=None, ge=0, le=100)
    occupancy_max: int | None = Field(default=None, ge=0, le=100)
    closed_to_arrival: bool = False
    closed_to_departure: bool = False
    stop_sell: bool = False
    channel_scope: str = "all"
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)
    rate_plan_ids: list[uuid.UUID] = Field(default_factory=list)
    reason: str | None = None


class RuleUpdate(RuleIn):
    version: int


class StatusIn(BaseModel):
    status: str
    version: int
    reason: str | None = None


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    rule_type: str
    rule_type_label: str
    status: str
    status_label: str
    priority: int
    applicable_for: str
    date_from: date
    date_to: date
    weekdays: list[int] = Field(default_factory=list)
    weekdays_label: str
    adjustment_direction: str
    adjustment_type: str
    adjustment_value: Decimal
    adjustment_label: str
    fixed_rate: Decimal | None = None
    min_stay: int | None = None
    max_stay: int | None = None
    advance_days_min: int | None = None
    advance_days_max: int | None = None
    occupancy_min: int | None = None
    occupancy_max: int | None = None
    closed_to_arrival: bool
    closed_to_departure: bool
    stop_sell: bool
    channel_scope: str
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)
    rate_plan_ids: list[uuid.UUID] = Field(default_factory=list)
    applies_to_label: str
    rate_plan_label: str
    restrictions_label: str
    conflict_count: int = 0
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by_name: str | None = None


class RuleStats(BaseModel):
    total: int
    published: int
    scheduled: int
    draft: int
    paused: int
    inactive: int
    conflicts: int


class RuleListOut(BaseModel):
    items: list[RuleOut]
    total: int
    stats: RuleStats


class ConflictOut(BaseModel):
    """Two published rules that would fight over the same night."""

    rule_id: uuid.UUID
    rule_name: str
    other_id: uuid.UUID
    other_name: str
    priority: int
    overlap_from: date
    overlap_to: date
    shared_weekdays: list[int] = Field(default_factory=list)
    detail: str


class SimulateIn(BaseModel):
    stay_date: date
    room_type_id: uuid.UUID
    rate_plan_id: uuid.UUID | None = None
    nights: int = 1
    lead_days: int | None = None


class SimulateStep(BaseModel):
    label: str
    detail: str
    amount: Decimal | None = None


class SimulateOut(BaseModel):
    stay_date: date
    base_rate: Decimal | None = None
    final_rate: Decimal | None = None
    stop_sell: bool = False
    steps: list[SimulateStep] = Field(default_factory=list)
    applied_rule_ids: list[uuid.UUID] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _validate(value: str | None, allowed: tuple[str, ...], field: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field}: {value!r}. Allowed: {', '.join(allowed)}",
        )


def _conflict_error(entity: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"{entity} was modified by someone else. Reload and try again.",
    )


def _money(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _num(value) -> str:
    return format(Decimal(value).normalize(), "f")


def _weekdays_label(days: list[int]) -> str:
    if not days:
        return "All Days"
    key = tuple(sorted(days))
    if key in WEEKDAY_SETS:
        return WEEKDAY_SETS[key]
    return "Selected Days (" + ", ".join(DAY_NAMES[d] for d in sorted(days)) + ")"


def _adjustment_label(row) -> str:
    if row["rule_type"] == "fixed_rate":
        return f"Set to ₹{Decimal(row['fixed_rate'] or 0):,.0f}"
    sign = "-" if row["adjustment_direction"] == "decrease" else "+"
    unit = "%" if row["adjustment_type"] == "percent" else "₹"
    value = _num(row["adjustment_value"])
    return f"{sign}{value}{unit}" if unit == "%" else f"{sign}₹{value}"


def _restrictions_label(row) -> str:
    bits: list[str] = []
    if row["min_stay"]:
        bits.append(f"Min stay {row['min_stay']}")
    if row["max_stay"]:
        bits.append(f"Max stay {row['max_stay']}")
    if row["advance_days_min"] is not None:
        bits.append(f"Book {row['advance_days_min']}+ days ahead")
    if row["advance_days_max"] is not None:
        bits.append(f"Within {row['advance_days_max']} days")
    # Said in the property's terms, not the column's. "80%+ sold" is what a
    # manager is actually deciding about.
    if row.get("occupancy_min") is not None and row.get("occupancy_max") is not None:
        bits.append(f"{row['occupancy_min']}-{row['occupancy_max']}% sold")
    elif row.get("occupancy_min") is not None:
        bits.append(f"{row['occupancy_min']}%+ sold")
    elif row.get("occupancy_max") is not None:
        bits.append(f"Under {row['occupancy_max'] + 1}% sold")
    if row["closed_to_arrival"]:
        bits.append("No arrivals")
    if row["closed_to_departure"]:
        bits.append("No departures")
    if row["stop_sell"]:
        bits.append("Stop sell")
    return " · ".join(bits) or "None"


_RULE_SELECT = """
    SELECT r.*, u.display_name AS updated_by_name,
           ARRAY(SELECT l.room_type_id FROM property.rate_rule_room_types l
                  WHERE l.rate_rule_id = r.id) AS room_type_ids,
           ARRAY(SELECT l.rate_plan_id FROM property.rate_rule_rate_plans l
                  WHERE l.rate_rule_id = r.id) AS rate_plan_ids,
           (SELECT string_agg(rt.name, ', ' ORDER BY rt.name)
              FROM property.rate_rule_room_types l
              JOIN property.room_types rt ON rt.id = l.room_type_id
             WHERE l.rate_rule_id = r.id) AS room_type_names,
           (SELECT string_agg(rp.code, ', ' ORDER BY rp.code)
              FROM property.rate_rule_rate_plans l
              JOIN property.rate_plans rp ON rp.id = l.rate_plan_id
             WHERE l.rate_rule_id = r.id) AS rate_plan_codes
    FROM property.rate_rules r
    LEFT JOIN iam.users u ON u.id = r.updated_by
"""


def _out(row, conflict_count: int = 0) -> RuleOut:
    data = dict(row)
    room_names = data.pop("room_type_names", None)
    plan_codes = data.pop("rate_plan_codes", None)
    for key in ("organization_id", "property_id", "created_by", "updated_by"):
        data.pop(key, None)
    weekdays = list(data.pop("weekdays") or [])
    return RuleOut(
        **data,
        weekdays=weekdays,
        weekdays_label=_weekdays_label(weekdays),
        rule_type_label=RULE_TYPE_LABELS.get(row["rule_type"], row["rule_type"]),
        status_label=STATUS_LABELS.get(row["status"], row["status"]),
        adjustment_label=_adjustment_label(row),
        restrictions_label=_restrictions_label(row),
        applies_to_label=room_names or "All Rooms",
        rate_plan_label=(
            plan_codes if row["applicable_for"] == "specific_rate_plans" and plan_codes
            else "All Rate Plans"
        ),
        conflict_count=conflict_count,
    )


def _check(body: RuleIn) -> None:
    _validate(body.rule_type, RULE_TYPES, "rule_type")
    _validate(body.status, STATUSES, "status")
    _validate(body.applicable_for, APPLICABLE_FOR, "applicable_for")
    _validate(body.adjustment_direction, DIRECTIONS, "adjustment_direction")
    _validate(body.adjustment_type, ADJUSTMENT_TYPES, "adjustment_type")
    if body.date_to < body.date_from:
        raise HTTPException(status_code=422, detail="End date is before start date")
    if body.priority < 0:
        raise HTTPException(status_code=422, detail="Priority cannot be negative")
    for day in body.weekdays:
        if day < 1 or day > 7:
            raise HTTPException(
                status_code=422, detail="Weekdays must be 1 (Monday) to 7 (Sunday)"
            )
    if body.rule_type == "fixed_rate":
        if body.fixed_rate is None or body.fixed_rate < 0:
            raise HTTPException(
                status_code=422, detail="A fixed rate rule needs a rate of zero or more"
            )
    else:
        if body.fixed_rate is not None:
            raise HTTPException(
                status_code=422,
                detail="An adjustment rule has no fixed rate; clear it or switch "
                       "the rule type",
            )
        if body.adjustment_value < 0:
            raise HTTPException(status_code=422, detail="Adjustment cannot be negative")
        if (
            body.adjustment_type == "percent"
            and body.adjustment_direction == "decrease"
            and body.adjustment_value > 100
        ):
            raise HTTPException(
                status_code=422, detail="A percentage discount cannot be more than 100%"
            )
    if body.min_stay is not None and body.min_stay < 1:
        raise HTTPException(status_code=422, detail="Minimum stay must be at least 1")
    if body.max_stay is not None and body.min_stay is not None \
            and body.max_stay < body.min_stay:
        raise HTTPException(
            status_code=422, detail="Maximum stay cannot be below minimum stay"
        )
    if body.advance_days_max is not None and body.advance_days_min is not None \
            and body.advance_days_max < body.advance_days_min:
        raise HTTPException(
            status_code=422,
            detail="The advance booking window ends before it starts",
        )
    if body.applicable_for == "specific_rate_plans" and not body.rate_plan_ids:
        raise HTTPException(
            status_code=422,
            detail="Choose at least one rate plan, or set this to apply to all",
        )


def _replace_links(
    db: Session, *, rule_id: uuid.UUID, property_id: uuid.UUID,
    room_type_ids: list[uuid.UUID], rate_plan_ids: list[uuid.UUID],
) -> None:
    for table, column, ids, owner in (
        ("rate_rule_room_types", "room_type_id", room_type_ids, "room_types"),
        ("rate_rule_rate_plans", "rate_plan_id", rate_plan_ids, "rate_plans"),
    ):
        db.execute(
            text(f"DELETE FROM property.{table} WHERE rate_rule_id = :id"),
            {"id": rule_id},
        )
        if not ids:
            continue
        owned = db.execute(
            text(
                f"SELECT id FROM property.{owner} "
                f"WHERE property_id = :prop AND id = ANY(:ids)"
            ),
            {"prop": property_id, "ids": ids},
        ).scalars().all()
        if len(set(owned)) != len(set(ids)):
            raise HTTPException(
                status_code=422,
                detail=f"One or more {owner.replace('_', ' ')} are not in this property",
            )
        db.execute(
            text(
                f"""
                INSERT INTO property.{table} (rate_rule_id, {column}, property_id)
                SELECT :id, unnest(CAST(:ids AS uuid[])), :prop
                """
            ),
            {"id": rule_id, "ids": ids, "prop": property_id},
        )


def _params(body: RuleIn, rule_id: uuid.UUID, prop: uuid.UUID, caller: Caller):
    return {
        "id": rule_id, "org": caller.organization_id, "prop": prop,
        "name": body.name.strip(), "description": body.description,
        "rule_type": body.rule_type, "status": body.status, "priority": body.priority,
        "applicable_for": body.applicable_for,
        "date_from": body.date_from, "date_to": body.date_to,
        "weekdays": sorted(set(body.weekdays)),
        "direction": body.adjustment_direction, "adj_type": body.adjustment_type,
        "adj_value": body.adjustment_value, "fixed_rate": body.fixed_rate,
        "min_stay": body.min_stay, "max_stay": body.max_stay,
        "adv_min": body.advance_days_min, "adv_max": body.advance_days_max,
        "occ_min": body.occupancy_min, "occ_max": body.occupancy_max,
        "cta": body.closed_to_arrival, "ctd": body.closed_to_departure,
        "stop_sell": body.stop_sell, "channel_scope": body.channel_scope,
        "actor": caller.user_id,
    }


def _conflicts(db: Session, property_id: uuid.UUID) -> list[ConflictOut]:
    """Published rules that share a priority and could hit the same night.

    Two rules only actually clash if they can reach the same room type and rate
    plan, and an empty link list means "all" — so the overlap test has to treat
    a rule with no links as matching everything.
    """
    rows = db.execute(
        text(
            """
            SELECT a.id AS rule_id, a.name AS rule_name, a.priority,
                   b.id AS other_id, b.name AS other_name,
                   GREATEST(a.date_from, b.date_from) AS overlap_from,
                   LEAST(a.date_to, b.date_to)        AS overlap_to,
                   CASE
                     WHEN a.weekdays = '{}' OR b.weekdays = '{}' THEN '{}'::int[]
                     ELSE ARRAY(SELECT unnest(a.weekdays)
                                INTERSECT SELECT unnest(b.weekdays))
                   END AS shared_weekdays,
                   (a.weekdays = '{}' OR b.weekdays = '{}'
                    OR a.weekdays && b.weekdays) AS days_overlap
            FROM property.rate_rules a
            JOIN property.rate_rules b
              ON b.property_id = a.property_id
             AND b.id > a.id
             AND b.priority = a.priority
             AND b.status = 'published'
             AND a.date_from <= b.date_to AND b.date_from <= a.date_to
            WHERE a.property_id = :prop AND a.status = 'published'
              -- Room types: either side unrestricted, or they share one.
              AND (NOT EXISTS (SELECT 1 FROM property.rate_rule_room_types x
                                WHERE x.rate_rule_id = a.id)
                   OR NOT EXISTS (SELECT 1 FROM property.rate_rule_room_types y
                                   WHERE y.rate_rule_id = b.id)
                   OR EXISTS (SELECT 1 FROM property.rate_rule_room_types x
                              JOIN property.rate_rule_room_types y
                                ON y.room_type_id = x.room_type_id
                               AND y.rate_rule_id = b.id
                              WHERE x.rate_rule_id = a.id))
              -- Rate plans: same reasoning.
              AND (a.applicable_for = 'all_rate_plans'
                   OR b.applicable_for = 'all_rate_plans'
                   OR EXISTS (SELECT 1 FROM property.rate_rule_rate_plans x
                              JOIN property.rate_rule_rate_plans y
                                ON y.rate_plan_id = x.rate_plan_id
                               AND y.rate_rule_id = b.id
                              WHERE x.rate_rule_id = a.id))
            ORDER BY a.priority, a.name
            """
        ),
        {"prop": property_id},
    ).mappings().all()

    out: list[ConflictOut] = []
    for r in rows:
        if not r["days_overlap"]:
            continue
        shared = list(r["shared_weekdays"] or [])
        days = _weekdays_label(shared) if shared else "the same days"
        out.append(
            ConflictOut(
                **{k: r[k] for k in (
                    "rule_id", "rule_name", "other_id", "other_name", "priority",
                    "overlap_from", "overlap_to")},
                shared_weekdays=shared,
                detail=f"Both are published at priority {r['priority']} and overlap "
                       f"from {r['overlap_from']} to {r['overlap_to']} on {days}. "
                       f"Give one a different priority to decide which wins.",
            )
        )
    return out


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------
@rule_router.get("/rate-rules", response_model=RuleListOut)
def list_rules(
    property_id: uuid.UUID,
    search: str | None = None,
    rule_status: str | None = Query(default=None, alias="status"),
    rate_plan_id: uuid.UUID | None = None,
    room_type_id: uuid.UUID | None = None,
    month: str | None = Query(default=None, description="YYYY-MM"),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The rule list behind screen 119."""
    assert_property_in_org(db, caller, property_id)
    _validate(rule_status, STATUSES, "status")

    where = ["r.property_id = :prop"]
    params: dict[str, Any] = {"prop": property_id}
    if search:
        where.append("(r.name ILIKE :q OR r.description ILIKE :q)")
        params["q"] = f"%{search}%"
    if rule_status:
        where.append("r.status = :status")
        params["status"] = rule_status
    if month:
        try:
            year, mon = (int(p) for p in month.split("-"))
            start = date(year, mon, 1)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=422, detail="Month must look like 2026-09"
            ) from exc
        end = date(year + (mon == 12), (mon % 12) + 1, 1) - timedelta(days=1)
        where.append("r.date_from <= :m_end AND r.date_to >= :m_start")
        params.update({"m_start": start, "m_end": end})
    if rate_plan_id:
        where.append(
            """(r.applicable_for = 'all_rate_plans'
                OR EXISTS (SELECT 1 FROM property.rate_rule_rate_plans l
                            WHERE l.rate_rule_id = r.id AND l.rate_plan_id = :rp))"""
        )
        params["rp"] = rate_plan_id
    if room_type_id:
        where.append(
            """(NOT EXISTS (SELECT 1 FROM property.rate_rule_room_types l
                             WHERE l.rate_rule_id = r.id)
                OR EXISTS (SELECT 1 FROM property.rate_rule_room_types l
                            WHERE l.rate_rule_id = r.id AND l.room_type_id = :rt))"""
        )
        params["rt"] = room_type_id

    rows = db.execute(
        text(f"{_RULE_SELECT} WHERE {' AND '.join(where)} ORDER BY r.priority, r.name"),
        params,
    ).mappings().all()

    clashes = _conflicts(db, property_id)
    per_rule: dict[uuid.UUID, int] = {}
    for c in clashes:
        per_rule[c.rule_id] = per_rule.get(c.rule_id, 0) + 1
        per_rule[c.other_id] = per_rule.get(c.other_id, 0) + 1

    counts = db.execute(
        text(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'published')  AS published,
                   count(*) FILTER (WHERE status = 'scheduled')  AS scheduled,
                   count(*) FILTER (WHERE status = 'draft')      AS draft,
                   count(*) FILTER (WHERE status = 'paused')     AS paused,
                   count(*) FILTER (WHERE status = 'inactive')   AS inactive
            FROM property.rate_rules WHERE property_id = :prop
            """
        ),
        {"prop": property_id},
    ).mappings().first()

    return RuleListOut(
        items=[_out(r, per_rule.get(r["id"], 0)) for r in rows],
        total=len(rows),
        stats=RuleStats(**counts, conflicts=len(clashes)),
    )


@rule_router.get("/rate-rules/conflicts", response_model=list[ConflictOut])
def rule_conflicts(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The Preview & Conflicts tab."""
    assert_property_in_org(db, caller, property_id)
    return _conflicts(db, property_id)


def _one(db: Session, rule_id: uuid.UUID, property_id: uuid.UUID) -> RuleOut:
    row = db.execute(
        text(f"{_RULE_SELECT} WHERE r.id = :id AND r.property_id = :prop"),
        {"id": rule_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Rate rule not found")
    return _out(row)


@rule_router.get("/rate-rules/{rule_id}", response_model=RuleOut)
def get_rule(
    rule_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    return _one(db, rule_id, property_id)


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
_INSERT = """
    INSERT INTO property.rate_rules
        (id, organization_id, property_id, name, description, rule_type, status,
         priority, applicable_for, date_from, date_to, weekdays,
         adjustment_direction, adjustment_type, adjustment_value, fixed_rate,
         min_stay, max_stay, advance_days_min, advance_days_max,
         occupancy_min, occupancy_max,
         closed_to_arrival, closed_to_departure, stop_sell, channel_scope,
         created_by, updated_by)
    VALUES (:id, :org, :prop, :name, :description, :rule_type, :status, :priority,
            :applicable_for, :date_from, :date_to, CAST(:weekdays AS integer[]),
            :direction, :adj_type, :adj_value, :fixed_rate, :min_stay, :max_stay,
            :adv_min, :adv_max, :occ_min, :occ_max,
            :cta, :ctd, :stop_sell, :channel_scope,
            :actor, :actor)
"""


@rule_router.post(
    "/rate-rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED
)
def create_rule(
    body: RuleIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check(body)
    rule_id = uuid.uuid4()
    try:
        db.execute(text(_INSERT), _params(body, rule_id, property_id, caller))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"A rule named {body.name!r} already exists"
        ) from exc
    _replace_links(
        db, rule_id=rule_id, property_id=property_id,
        room_type_ids=body.room_type_ids, rate_plan_ids=body.rate_plan_ids,
    )
    record_audit(
        db, action="rate_rule.create", entity_type="rate_rule", entity_id=str(rule_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={"name": body.name, "status": body.status,
               "window": f"{body.date_from} to {body.date_to}"},
        reason=body.reason,
    )
    return _one(db, rule_id, property_id)


@rule_router.put("/rate-rules/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: uuid.UUID,
    property_id: uuid.UUID,
    body: RuleUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check(body)
    params = _params(body, rule_id, property_id, caller)
    params["version"] = body.version
    try:
        result = db.execute(
            text(
                """
                UPDATE property.rate_rules
                   SET name = :name, description = :description,
                       rule_type = :rule_type, status = :status,
                       priority = :priority, applicable_for = :applicable_for,
                       date_from = :date_from, date_to = :date_to,
                       weekdays = CAST(:weekdays AS integer[]),
                       adjustment_direction = :direction,
                       adjustment_type = :adj_type, adjustment_value = :adj_value,
                       fixed_rate = :fixed_rate, min_stay = :min_stay,
                       max_stay = :max_stay, advance_days_min = :adv_min,
                       advance_days_max = :adv_max,
                       occupancy_min = :occ_min, occupancy_max = :occ_max,
                       closed_to_arrival = :cta,
                       closed_to_departure = :ctd, stop_sell = :stop_sell,
                       channel_scope = :channel_scope, updated_at = now(),
                       updated_by = :actor, version = version + 1
                 WHERE id = :id AND property_id = :prop AND version = :version
                """
            ),
            params,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"A rule named {body.name!r} already exists"
        ) from exc
    if result.rowcount == 0:
        exists = db.execute(
            text("SELECT 1 FROM property.rate_rules WHERE id = :id AND property_id = :prop"),
            {"id": rule_id, "prop": property_id},
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="Rate rule not found")
        raise _conflict_error("Rate rule")
    _replace_links(
        db, rule_id=rule_id, property_id=property_id,
        room_type_ids=body.room_type_ids, rate_plan_ids=body.rate_plan_ids,
    )
    record_audit(
        db, action="rate_rule.update", entity_type="rate_rule", entity_id=str(rule_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after={"name": body.name, "status": body.status},
        reason=body.reason,
    )
    return _one(db, rule_id, property_id)


@rule_router.post("/rate-rules/{rule_id}/status", response_model=RuleOut)
def set_rule_status(
    rule_id: uuid.UUID,
    property_id: uuid.UUID,
    body: StatusIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Publish, pause, deactivate or return a rule to draft."""
    assert_property_in_org(db, caller, property_id)
    _validate(body.status, STATUSES, "status")
    row = db.execute(
        text(
            "SELECT status, date_from FROM property.rate_rules "
            "WHERE id = :id AND property_id = :prop"
        ),
        {"id": rule_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Rate rule not found")

    target = body.status
    # A rule published before its window opens is scheduled, not live. Saying so
    # keeps the list honest instead of showing a rule as active for weeks.
    if target == "published":
        today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
        if row["date_from"] > today:
            target = "scheduled"

    result = db.execute(
        text(
            """
            UPDATE property.rate_rules
               SET status = :status, updated_at = now(), updated_by = :actor,
                   version = version + 1
             WHERE id = :id AND property_id = :prop AND version = :version
            """
        ),
        {
            "status": target, "id": rule_id, "prop": property_id,
            "actor": caller.user_id, "version": body.version,
        },
    )
    if result.rowcount == 0:
        raise _conflict_error("Rate rule")
    record_audit(
        db, action=f"rate_rule.{target}", entity_type="rate_rule",
        entity_id=str(rule_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={"status": row["status"]}, after={"status": target}, reason=body.reason,
    )
    return _one(db, rule_id, property_id)


@rule_router.post(
    "/rate-rules/{rule_id}/duplicate", response_model=RuleOut,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_rule(
    rule_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    """Copy a rule. The copy starts as a draft so it cannot price anything yet."""
    assert_property_in_org(db, caller, property_id)
    source = db.execute(
        text("SELECT * FROM property.rate_rules WHERE id = :id AND property_id = :prop"),
        {"id": rule_id, "prop": property_id},
    ).mappings().first()
    if source is None:
        raise HTTPException(status_code=404, detail="Rate rule not found")

    taken = set(
        db.execute(
            text("SELECT name FROM property.rate_rules WHERE property_id = :prop"),
            {"prop": property_id},
        ).scalars().all()
    )
    suffix = 2
    base = source["name"][:90]
    while f"{base} ({suffix})" in taken:
        suffix += 1
    new_name = f"{base} ({suffix})"

    new_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO property.rate_rules
                (id, organization_id, property_id, name, description, rule_type,
                 status, priority, applicable_for, date_from, date_to, weekdays,
                 adjustment_direction, adjustment_type, adjustment_value,
                 fixed_rate, min_stay, max_stay, advance_days_min,
                 advance_days_max, closed_to_arrival, closed_to_departure,
                 stop_sell, channel_scope, created_by, updated_by)
            SELECT :new_id, organization_id, property_id, :name, description,
                   rule_type, 'draft', priority, applicable_for, date_from,
                   date_to, weekdays, adjustment_direction, adjustment_type,
                   adjustment_value, fixed_rate, min_stay, max_stay,
                   advance_days_min, advance_days_max, closed_to_arrival,
                   closed_to_departure, stop_sell, channel_scope, :actor, :actor
            FROM property.rate_rules WHERE id = :id
            """
        ),
        {"new_id": new_id, "name": new_name, "id": rule_id, "actor": caller.user_id},
    )
    for table, column in (
        ("rate_rule_room_types", "room_type_id"),
        ("rate_rule_rate_plans", "rate_plan_id"),
    ):
        db.execute(
            text(
                f"""
                INSERT INTO property.{table} (rate_rule_id, {column}, property_id)
                SELECT :new_id, {column}, property_id
                FROM property.{table} WHERE rate_rule_id = :id
                """
            ),
            {"new_id": new_id, "id": rule_id},
        )
    record_audit(
        db, action="rate_rule.duplicate", entity_type="rate_rule",
        entity_id=str(new_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"copied_from": source["name"], "name": new_name},
    )
    return _one(db, new_id, property_id)


# --------------------------------------------------------------------------
# Simulate
# --------------------------------------------------------------------------
@rule_router.post("/rate-rules/simulate", response_model=SimulateOut)
def simulate(
    property_id: uuid.UUID,
    body: SimulateIn,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """What one night would actually cost, and which rules got it there.

    Runs the real chain — the calendar's rate for the date (falling back to the
    room type), then the rate plan's own pricing, then each applicable rule in
    priority order — so the preview and the configuration cannot disagree.
    """
    assert_property_in_org(db, caller, property_id)
    if body.nights < 1:
        raise HTTPException(status_code=422, detail="Nights must be at least 1")

    room_type = db.execute(
        text(
            "SELECT id, name, base_rate FROM property.room_types "
            "WHERE id = :id AND property_id = :prop"
        ),
        {"id": body.room_type_id, "prop": property_id},
    ).mappings().first()
    if room_type is None:
        raise HTTPException(status_code=404, detail="Room type not found")

    steps: list[SimulateStep] = []
    skipped: list[str] = []

    override = db.execute(
        text(
            "SELECT rate FROM property.rate_calendar_days "
            "WHERE property_id = :prop AND room_type_id = :rt AND stay_date = :d"
        ),
        {"prop": property_id, "rt": body.room_type_id, "d": body.stay_date},
    ).scalar_one_or_none()
    rate = _money(override) if override is not None else _money(room_type["base_rate"])
    steps.append(
        SimulateStep(
            label="Base rate",
            detail=(f"{room_type['name']} — set for this date on the calendar"
                    if override is not None
                    else f"{room_type['name']} — room type base rate"),
            amount=rate,
        )
    )
    if rate is None:
        return SimulateOut(stay_date=body.stay_date, steps=steps,
                           skipped=["This room type has no base rate."])

    plan = None
    if body.rate_plan_id:
        plan = db.execute(
            text(
                "SELECT id, code, name, flat_rate, adjustment_direction, "
                "adjustment_type, adjustment_value FROM property.rate_plans "
                "WHERE id = :id AND property_id = :prop"
            ),
            {"id": body.rate_plan_id, "prop": property_id},
        ).mappings().first()
        if plan is None:
            raise HTTPException(status_code=404, detail="Rate plan not found")
        if plan["flat_rate"] is not None:
            rate = _money(plan["flat_rate"])
            steps.append(SimulateStep(
                label=f"Rate plan {plan['code']}",
                detail="Fixed price — the room type rate does not apply",
                amount=rate,
            ))
        else:
            signed = (
                -plan["adjustment_value"]
                if plan["adjustment_direction"] == "decrease"
                else plan["adjustment_value"]
            )
            rate = _money(
                rate * (1 + signed / Decimal(100))
                if plan["adjustment_type"] == "percent" else rate + signed
            )
            steps.append(SimulateStep(
                label=f"Rate plan {plan['code']}",
                detail=f"{plan['adjustment_direction']} "
                       f"{_num(plan['adjustment_value'])}"
                       f"{'%' if plan['adjustment_type'] == 'percent' else ''}",
                amount=rate,
            ))

    iso_day = body.stay_date.isoweekday()
    lead = body.lead_days
    if lead is None:
        today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
        lead = (body.stay_date - today).days

    rules = db.execute(
        text(
            f"""
            {_RULE_SELECT}
            WHERE r.property_id = :prop
              AND r.status = 'published'
              AND r.date_from <= :d AND r.date_to >= :d
              AND (r.weekdays = '{{}}' OR :dow = ANY(r.weekdays))
              AND (NOT EXISTS (SELECT 1 FROM property.rate_rule_room_types l
                                WHERE l.rate_rule_id = r.id)
                   OR EXISTS (SELECT 1 FROM property.rate_rule_room_types l
                               WHERE l.rate_rule_id = r.id
                                 AND l.room_type_id = :rt))
            ORDER BY r.priority, r.name
            """
        ),
        {"prop": property_id, "d": body.stay_date, "dow": iso_day,
         "rt": body.room_type_id},
    ).mappings().all()

    applied: list[uuid.UUID] = []
    stop = False
    for r in rules:
        if r["applicable_for"] == "specific_rate_plans":
            plan_ids = list(r["rate_plan_ids"] or [])
            if body.rate_plan_id is None or body.rate_plan_id not in plan_ids:
                skipped.append(f"{r['name']} — applies to other rate plans")
                continue
        if r["min_stay"] and body.nights < r["min_stay"]:
            skipped.append(
                f"{r['name']} — needs a stay of {r['min_stay']} night(s), this is "
                f"{body.nights}"
            )
            continue
        if r["max_stay"] and body.nights > r["max_stay"]:
            skipped.append(f"{r['name']} — only up to {r['max_stay']} night(s)")
            continue
        if r["advance_days_min"] is not None and lead < r["advance_days_min"]:
            skipped.append(
                f"{r['name']} — needs booking {r['advance_days_min']}+ days ahead, "
                f"this is {lead}"
            )
            continue
        if r["advance_days_max"] is not None and lead > r["advance_days_max"]:
            skipped.append(
                f"{r['name']} — only within {r['advance_days_max']} days of arrival"
            )
            continue

        if r["stop_sell"]:
            stop = True
        if r["rule_type"] == "fixed_rate":
            rate = _money(r["fixed_rate"])
            detail = f"priority {r['priority']} — set to a fixed rate"
        else:
            signed = (
                -r["adjustment_value"] if r["adjustment_direction"] == "decrease"
                else r["adjustment_value"]
            )
            rate = max(Decimal(0), _money(
                rate * (1 + signed / Decimal(100))
                if r["adjustment_type"] == "percent" else rate + signed
            ))
            detail = f"priority {r['priority']} — {_adjustment_label(r)}"
        applied.append(r["id"])
        steps.append(SimulateStep(label=r["name"], detail=detail, amount=rate))

    return SimulateOut(
        stay_date=body.stay_date,
        base_rate=_money(override if override is not None else room_type["base_rate"]),
        final_rate=rate, stop_sell=stop, steps=steps,
        applied_rule_ids=applied, skipped=skipped,
    )


# --------------------------------------------------------------------------
# Publishing rules into the rate calendar
#
# The step that was missing. Everything above defines rules and previews them;
# this is what makes a guest pay the weekend price. See rate_publish for why
# the calendar is the integration point rather than each pricing path.
# --------------------------------------------------------------------------
class PublishIn(BaseModel):
    date_from: date
    date_to: date
    #: Empty means every active room type.
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Preview one rule's effect on its own. Omitted, every published price
    #: rule is layered, which is what the calendar must end up holding.
    rule_id: uuid.UUID | None = None


class PublishDay(BaseModel):
    stay_date: date
    room_type_id: uuid.UUID
    room_type_name: str
    base_rate: Decimal
    current_rate: Decimal | None
    new_rate: Decimal
    #: write / clear / none / skip — see rate_publish.DayPlan.
    action: str
    #: Percent of sellable rooms already sold for this night, where known.
    occupancy: int | None
    applied: list[str]
    skipped: str | None


class PublishPreview(BaseModel):
    days: list[PublishDay]
    #: Counts, so the screen can lead with the summary rather than make
    #: somebody add up a thousand rows.
    total_days: int
    changing: int
    protected: int
    unchanged: int
    #: Nights whose rule no longer applies, whose stored rate is removed so
    #: they follow the room's base rate again.
    clearing: int


class PublishResult(BaseModel):
    written: int
    cleared: int
    protected: int
    message: str


def _preview(db: Session, property_id: uuid.UUID,
             body: PublishIn) -> tuple[rate_publish.Plan, PublishPreview]:
    try:
        computed = rate_publish.plan(
            db, property_id=property_id, date_from=body.date_from,
            date_to=body.date_to,
            room_type_ids=list(body.room_type_ids) or None,
            rule_id=body.rule_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    days = [
        PublishDay(
            stay_date=d.stay_date, room_type_id=d.room_type_id,
            room_type_name=d.room_type_name, base_rate=d.base_rate,
            current_rate=d.current_rate, new_rate=d.new_rate,
            action=d.action, occupancy=d.occupancy,
            applied=d.applied, skipped=d.skipped,
        )
        for d in computed.days
    ]
    changing = len(computed.changed)
    protected = len(computed.protected)
    clearing = len(computed.to_clear)
    return computed, PublishPreview(
        days=days, total_days=len(days), changing=changing,
        protected=protected, clearing=clearing,
        unchanged=len(days) - changing - protected,
    )


@rule_router.post("/properties/{property_id}/rate-rules/preview-publish",
                  response_model=PublishPreview)
def preview_publish(
    property_id: uuid.UUID,
    body: PublishIn,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """What publishing would do to the calendar. Changes nothing."""
    assert_property_in_org(db, caller, property_id)
    return _preview(db, property_id, body)[1]


@rule_router.post("/properties/{property_id}/rate-rules/publish",
                  response_model=PublishResult)
def publish_rules(
    property_id: uuid.UUID,
    body: PublishIn,
    caller: Caller = Depends(require_permission("rooms", "update")),
    db: Session = Depends(get_session),
):
    """Write the rules' rates into the calendar the whole system prices from."""
    assert_property_in_org(db, caller, property_id)
    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :id"),
        {"id": property_id},
    ).scalar_one()

    # Computed by the same function the preview used, so what was approved on
    # screen is what lands.
    computed, summary = _preview(db, property_id, body)
    written, cleared = rate_publish.publish(
        db, property_id=property_id, organization_id=org,
        computed=computed, actor_id=caller.user_id,
    )
    record_audit(
        db, actor_subject=caller.subject, action="rate_rules.publish",
        entity_type="rate_calendar", entity_id=str(property_id),
        organization_id=org, property_id=property_id,
        after={"from": str(body.date_from), "to": str(body.date_to),
               "written": written, "cleared": cleared,
               "protected": summary.protected},
    )
    parts = [f"{written} night(s) priced"]
    if cleared:
        parts.append(f"{cleared} returned to the base rate")
    if summary.protected:
        parts.append(f"{summary.protected} left as set by hand")
    return PublishResult(
        written=written, cleared=cleared, protected=summary.protected,
        message=" — ".join(parts) + ".",
    )
