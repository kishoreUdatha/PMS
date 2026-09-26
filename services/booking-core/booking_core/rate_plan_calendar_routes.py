"""A rate plan's own price and stay rules, night by night.

The Rates & Inventory grid prices a *room type*; every rate plan on it sells
at that price plus the plan's adjustment. That cannot express two things a
hotel -- and a channel manager -- expects: Bed & Breakfast priced on its own
for one night, and one plan closed to arrival while another stays open.

These routes read and write ``property.rate_plan_calendar_days``: per plan,
per night overrides of the price, minimum and maximum stay, closed to arrival,
closed to departure and stop sell. A field left empty falls through to the
room calendar, the rate rules and the plan's defaults. The read returns the
*effective* values -- the same calculation the channel sync sends -- with a
flag for each one the plan set itself.

A write applies every change it carries in one transaction, so a range edit
or a multi-plan edit reaches the channel manager as one update.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import channel_push as calc
from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

plan_calendar_router = APIRouter(tags=["rate-plan-calendar"],
                                 route_class=TransactionalRoute)

FIELDS = ("rate", "min_stay", "max_stay", "closed_to_arrival",
          "closed_to_departure", "stop_sell")
MAX_SPAN_DAYS = 550


class PlanNight(BaseModel):
    stay_date: date
    rate: Decimal | None = None
    min_stay: int
    max_stay: int
    closed_to_arrival: bool
    closed_to_departure: bool
    stop_sell: bool
    #: Which of the above this plan set for itself on this night.
    overridden: list[str] = []


class PlanCalendar(BaseModel):
    rate_plan_id: uuid.UUID
    rate_plan_name: str
    room_type_id: uuid.UUID | None = None
    room_type_name: str | None = None
    nights: list[PlanNight]


def _plan(db: Session, property_id, rate_plan_id):
    row = db.execute(
        text(
            """
            SELECT rp.id AS rate_plan_id, rp.name, rp.flat_rate,
                   rp.adjustment_direction, rp.adjustment_type,
                   rp.adjustment_value,
                   count(x.room_type_id) AS room_types,
                   min(x.room_type_id::text) AS room_type_id,
                   min(rt.name) AS room_type_name
            FROM property.rate_plans rp
            LEFT JOIN property.rate_plan_room_types x ON x.rate_plan_id = rp.id
            LEFT JOIN property.room_types rt ON rt.id = x.room_type_id
            WHERE rp.id = :p AND rp.property_id = :prop
            GROUP BY rp.id
            """
        ),
        {"p": rate_plan_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such rate plan.")
    return row


@plan_calendar_router.get("/rate-plan-calendar", response_model=PlanCalendar)
def get_plan_calendar(
    property_id: uuid.UUID,
    rate_plan_id: uuid.UUID,
    date_from: date,
    date_to: date,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """What this plan sells for, and its stay rules, on each night."""
    assert_property_in_org(db, caller, property_id)
    if date_to < date_from or (date_to - date_from).days > MAX_SPAN_DAYS:
        raise HTTPException(status_code=422, detail="Choose a shorter range.")
    plan = _plan(db, property_id, rate_plan_id)
    single = plan["room_types"] == 1
    rates = calc.plan_rates(db, plan, date_from, date_to) if (
        single or plan["flat_rate"] is not None) else {}
    rules = calc.plan_restrictions(db, rate_plan_id, plan["room_type_id"],
                                   date_from, date_to) if single else {}
    own = {r["stay_date"]: r for r in db.execute(
        text("SELECT * FROM property.rate_plan_calendar_days "
             "WHERE rate_plan_id = :p AND stay_date BETWEEN :f AND :t"),
        {"p": rate_plan_id, "f": date_from, "t": date_to},
    ).mappings().all()}
    nights = []
    for i in range((date_to - date_from).days + 1):
        d = date_from + timedelta(days=i)
        r = rules.get(d, {})
        o = own.get(d, {})
        nights.append(PlanNight(
            stay_date=d, rate=rates.get(d),
            min_stay=r.get("min_stay_arrival", 1),
            max_stay=r.get("max_stay", 0),
            closed_to_arrival=r.get("closed_to_arrival", False),
            closed_to_departure=r.get("closed_to_departure", False),
            stop_sell=r.get("stop_sell", False),
            overridden=[f for f in FIELDS if o and o.get(f) is not None],
        ))
    return PlanCalendar(
        rate_plan_id=rate_plan_id, rate_plan_name=plan["name"],
        room_type_id=plan["room_type_id"] if single else None,
        room_type_name=plan["room_type_name"] if single else None,
        nights=nights)


class PlanChange(BaseModel):
    rate_plan_id: uuid.UUID
    date_from: date
    date_to: date
    #: ISO weekdays (1 = Monday ... 7 = Sunday). Empty = every night.
    weekdays: list[int] = []
    rate: Decimal | None = Field(default=None, ge=0)
    min_stay: int | None = Field(default=None, ge=1, le=99)
    max_stay: int | None = Field(default=None, ge=0, le=365)
    closed_to_arrival: bool | None = None
    closed_to_departure: bool | None = None
    stop_sell: bool | None = None
    #: Fields to hand back to the room calendar, rules and plan defaults.
    clear: list[str] = []


class PlanChangesIn(BaseModel):
    changes: list[PlanChange] = Field(min_length=1, max_length=50)


class PlanChangesOut(BaseModel):
    nights_changed: int


@plan_calendar_router.put("/rate-plan-calendar", response_model=PlanChangesOut)
def set_plan_calendar(
    property_id: uuid.UUID,
    body: PlanChangesIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Set (or clear) plan prices and stay rules over nights, in one go."""
    assert_property_in_org(db, caller, property_id)
    org = db.execute(text("SELECT organization_id FROM iam.properties "
                          "WHERE id = :p"), {"p": property_id}).scalar()
    total = 0
    for ch in body.changes:
        _plan(db, property_id, ch.rate_plan_id)
        if ch.date_to < ch.date_from or \
                (ch.date_to - ch.date_from).days > MAX_SPAN_DAYS:
            raise HTTPException(status_code=422,
                                detail="Choose a shorter date range.")
        bad = [f for f in ch.clear if f not in FIELDS]
        if bad:
            raise HTTPException(status_code=422,
                                detail=f"Unknown fields to clear: {bad}")
        if any(w < 1 or w > 7 for w in ch.weekdays):
            raise HTTPException(status_code=422,
                                detail="Weekdays are 1 (Monday) to 7.")
        sets = {f: getattr(ch, f) for f in FIELDS
                if getattr(ch, f) is not None and f not in ch.clear}
        if not sets and not ch.clear:
            raise HTTPException(status_code=422, detail="Nothing to change.")
        if ("min_stay" in sets and "max_stay" in sets and sets["max_stay"]
                and sets["max_stay"] < sets["min_stay"]):
            raise HTTPException(status_code=422,
                                detail="Maximum stay is shorter than minimum.")
        days = [ch.date_from + timedelta(days=i)
                for i in range((ch.date_to - ch.date_from).days + 1)]
        if ch.weekdays:
            days = [d for d in days if d.isoweekday() in ch.weekdays]
        if not days:
            continue
        assign = ", ".join(
            [f"{f} = EXCLUDED.{f}" for f in sets]
            + [f"{f} = NULL" for f in ch.clear]
            + ["updated_by = EXCLUDED.updated_by", "updated_at = now()"])
        cols = ", ".join(sets)
        db.execute(
            text(
                f"""
                INSERT INTO property.rate_plan_calendar_days
                    (organization_id, property_id, rate_plan_id, stay_date,
                     updated_by{", " + cols if cols else ""})
                SELECT :org, :prop, :plan, d, :who
                       {"".join(f", CAST(:{f} AS {_sqltype(f)})" for f in sets)}
                FROM unnest(CAST(:days AS date[])) AS d
                ON CONFLICT (rate_plan_id, stay_date) DO UPDATE SET {assign}
                """
            ),
            {"org": org, "prop": property_id, "plan": ch.rate_plan_id,
             "who": caller.user_id, "days": days, **sets},
        )
        total += len(days)
    # Rows left with nothing set are just noise.
    db.execute(text(
        "DELETE FROM property.rate_plan_calendar_days WHERE property_id = :p "
        "AND rate IS NULL AND min_stay IS NULL AND max_stay IS NULL "
        "AND closed_to_arrival IS NULL AND closed_to_departure IS NULL "
        "AND stop_sell IS NULL"), {"p": property_id})
    record_audit(
        db, action="rate_plan_calendar.updated", entity_type="property",
        entity_id=str(property_id), organization_id=org,
        property_id=property_id, actor_subject=caller.subject,
        after={"changes": [c.model_dump(mode="json") for c in body.changes]},
    )
    return PlanChangesOut(nights_changed=total)


def _sqltype(field: str) -> str:
    return {"rate": "numeric", "min_stay": "int", "max_stay": "int"}.get(
        field, "boolean")
