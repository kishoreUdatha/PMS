"""Rates, Restrictions and Inventory Calendar API (screen 034).

The grid is a *view*, not a store. Only overrides live in
``property.rate_calendar_days``; every cell nobody has touched falls back to the
room type, and availability is derived at read time from the room inventory and
``booking.room_type_inventory_days``. Nothing on this screen keeps a second copy
of a number that is owned elsewhere.

Bulk update runs the same code for preview and publish — the preview is the
publish with the write skipped — so what the panel promises and what the button
does cannot drift apart.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
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

calendar_router = APIRouter(tags=["rate-calendar"], route_class=TransactionalRoute)

MAX_WINDOW_DAYS = 60
CHANGE_MODES = ("increase", "decrease", "set_to")
VALUE_TYPES = ("percent", "amount")


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class CalendarCell(BaseModel):
    stay_date: date
    rate: Decimal | None = None
    rate_is_override: bool = False
    min_stay: int
    min_stay_is_override: bool = False
    stop_sell: bool = False
    capacity: int
    out_of_service: int
    reserved: int
    available: int


class CalendarRow(BaseModel):
    room_type_id: uuid.UUID
    room_type_name: str
    units: int
    base_rate: Decimal | None = None
    photo_url: str | None = None
    days: list[CalendarCell] = Field(default_factory=list)


class ForecastPoint(BaseModel):
    stay_date: date
    occupancy_pct: float


class CalendarOut(BaseModel):
    date_from: date
    date_to: date
    dates: list[date]
    rows: list[CalendarRow]
    forecast: list[ForecastPoint]
    # When a rate plan is being viewed the rates are that plan's prices, which
    # are derived — so the grid must not offer to edit them.
    rate_plan_id: uuid.UUID | None = None
    rate_plan_name: str | None = None
    editable: bool = True


class CellUpdate(BaseModel):
    """One inline edit in the grid. Omitted fields are left alone."""

    room_type_id: uuid.UUID
    stay_date: date
    rate: Decimal | None = None
    clear_rate: bool = False
    min_stay: int | None = None
    clear_min_stay: bool = False
    stop_sell: bool | None = None


class BulkUpdateIn(BaseModel):
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)  # empty = all
    dates: list[date] = Field(min_length=1)
    change: str = "increase"
    value_type: str = "percent"
    value: Decimal = Decimal("0")
    update_min_stay: bool = False
    min_stay: int | None = None
    update_stop_sell: bool = False
    stop_sell: bool | None = None
    preview_only: bool = True
    reason: str | None = None


class BulkPreviewRow(BaseModel):
    room_type_id: uuid.UUID
    room_type_name: str
    current_avg: Decimal | None = None
    new_avg: Decimal | None = None


class BulkUpdateOut(BaseModel):
    rows: list[BulkPreviewRow]
    room_type_count: int
    date_count: int
    cell_count: int
    applied: bool


class CopyRatesIn(BaseModel):
    """Copy a source window's rates onto a target window, day by day."""

    source_from: date
    source_to: date
    target_from: date
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)
    include_restrictions: bool = True
    preview_only: bool = True


class CopyRatesOut(BaseModel):
    days: int
    room_type_count: int
    cell_count: int
    applied: bool
    target_to: date


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _validate(value: str, allowed: tuple[str, ...], field: str) -> None:
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field}: {value!r}. Allowed: {', '.join(allowed)}",
        )


def _window(date_from: date, date_to: date) -> list[date]:
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="End date is before start date")
    span = (date_to - date_from).days + 1
    if span > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Date range is {span} days; the calendar shows at most "
                   f"{MAX_WINDOW_DAYS} at a time",
        )
    return [date_from + timedelta(days=i) for i in range(span)]


def _money(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _room_types(db: Session, property_id: uuid.UUID, only: list[uuid.UUID] | None):
    where = ["rt.property_id = :prop", "rt.status = 'active'"]
    params: dict[str, Any] = {"prop": property_id}
    if only:
        where.append("rt.id = ANY(:ids)")
        params["ids"] = only
    return db.execute(
        text(
            f"""
            SELECT rt.id, rt.name, rt.base_rate,
                   (SELECT count(*) FROM property.rooms r
                     WHERE r.room_type_id = rt.id AND r.status = 'active') AS units
            FROM property.room_types rt
            WHERE {' AND '.join(where)}
            ORDER BY rt.name
            """
        ),
        params,
    ).mappings().all()


def _new_rate(current: Decimal, body: BulkUpdateIn) -> Decimal:
    """Apply the panel's change to one cell."""
    if body.change == "set_to":
        result = body.value
    elif body.value_type == "percent":
        factor = body.value / Decimal(100)
        result = current * (1 + factor if body.change == "increase" else 1 - factor)
    else:
        result = current + (body.value if body.change == "increase" else -body.value)
    return max(Decimal(0), _money(result))


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------
@calendar_router.get("/rate-calendar", response_model=CalendarOut)
def rate_calendar(
    property_id: uuid.UUID,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    room_type_id: uuid.UUID | None = None,
    rate_plan_id: uuid.UUID | None = None,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The grid: one row per room type, one column per date.

    With ``rate_plan_id`` the rates shown are that plan's nightly prices rather
    than the room type's own, and the grid becomes read-only — the plan's price
    is derived from the room type rate, so it is not a number you can type over.
    """
    assert_property_in_org(db, caller, property_id)
    dates = _window(date_from, date_to)
    types = _room_types(db, property_id, [room_type_id] if room_type_id else None)
    if not types:
        return CalendarOut(
            date_from=date_from, date_to=date_to, dates=dates, rows=[], forecast=[]
        )

    params = {"prop": property_id, "from": date_from, "to": date_to}

    overrides = {
        (r["room_type_id"], r["stay_date"]): r
        for r in db.execute(
            text(
                """
                SELECT room_type_id, stay_date, rate, min_stay, stop_sell
                FROM property.rate_calendar_days
                WHERE property_id = :prop AND stay_date BETWEEN :from AND :to
                """
            ),
            params,
        ).mappings().all()
    }

    # Room-type level demand: units sold or held before a physical room is
    # assigned. Owned by the inventory path, so read, never recomputed.
    inventory = {
        (r["room_type_id"], r["stay_date"]): r["taken"]
        for r in db.execute(
            text(
                """
                SELECT room_type_id, stay_date,
                       (reserved_units + held_units + allotment_units) AS taken
                FROM booking.room_type_inventory_days
                WHERE property_id = :prop AND stay_date BETWEEN :from AND :to
                """
            ),
            params,
        ).mappings().all()
    }

    # Rooms physically unavailable that night, from the blocks that own it.
    blocked = {
        (r["room_type_id"], r["stay_date"]): r["blocked"]
        for r in db.execute(
            text(
                """
                -- generate_series over dates yields timestamps; cast back so
                -- the result keys match the date objects used to index it.
                SELECT r.room_type_id, CAST(d.stay_date AS date) AS stay_date,
                       count(DISTINCT b.room_id) AS blocked
                FROM generate_series(CAST(:from AS date), CAST(:to AS date),
                                     '1 day') AS d(stay_date)
                JOIN booking.room_blocks b
                  ON b.property_id = :prop AND b.status = 'active'
                 AND d.stay_date BETWEEN b.start_date AND b.end_date
                JOIN property.rooms r ON r.id = b.room_id
                GROUP BY r.room_type_id, CAST(d.stay_date AS date)
                """
            ),
            params,
        ).mappings().all()
    }

    from .rooms_routes import photo_url  # local import avoids an import cycle

    photo_rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (room_type_id) room_type_id, url, storage_key
            FROM property.room_type_photos
            WHERE property_id = :prop
            ORDER BY room_type_id, is_primary DESC, sort_order
            """
        ),
        {"prop": property_id},
    ).mappings().all()
    photos = {r["room_type_id"]: photo_url(r["url"], r["storage_key"]) for r in photo_rows}

    rows: list[CalendarRow] = []
    per_date_capacity: dict[date, int] = {d: 0 for d in dates}
    per_date_taken: dict[date, int] = {d: 0 for d in dates}

    for rt in types:
        cells: list[CalendarCell] = []
        for d in dates:
            ov = overrides.get((rt["id"], d))
            taken = int(inventory.get((rt["id"], d), 0) or 0)
            oos = int(blocked.get((rt["id"], d), 0) or 0)
            capacity = int(rt["units"] or 0)
            cells.append(
                CalendarCell(
                    stay_date=d,
                    rate=_money(ov["rate"]) if ov and ov["rate"] is not None
                    else _money(rt["base_rate"]),
                    rate_is_override=bool(ov and ov["rate"] is not None),
                    min_stay=(ov["min_stay"] if ov and ov["min_stay"] is not None else 1),
                    min_stay_is_override=bool(ov and ov["min_stay"] is not None),
                    stop_sell=bool(ov["stop_sell"]) if ov else False,
                    capacity=capacity,
                    out_of_service=oos,
                    reserved=taken,
                    available=max(capacity - oos - taken, 0),
                )
            )
            per_date_capacity[d] += capacity
            per_date_taken[d] += taken + oos
        rows.append(
            CalendarRow(
                room_type_id=rt["id"], room_type_name=rt["name"],
                units=int(rt["units"] or 0), base_rate=_money(rt["base_rate"]),
                photo_url=photos.get(rt["id"]), days=cells,
            )
        )

    forecast = [
        ForecastPoint(
            stay_date=d,
            occupancy_pct=round(
                (per_date_taken[d] / per_date_capacity[d]) * 100, 1
            ) if per_date_capacity[d] else 0.0,
        )
        for d in dates
    ]

    plan_name = None
    if rate_plan_id is not None:
        plan = db.execute(
            text(
                """
                SELECT rp.id, rp.name, rp.flat_rate, rp.adjustment_direction,
                       rp.adjustment_type, rp.adjustment_value,
                       ARRAY(SELECT l.room_type_id
                               FROM property.rate_plan_room_types l
                              WHERE l.rate_plan_id = rp.id) AS room_type_ids
                FROM property.rate_plans rp
                WHERE rp.id = :id AND rp.property_id = :prop
                """
            ),
            {"id": rate_plan_id, "prop": property_id},
        ).mappings().first()
        if plan is None:
            raise HTTPException(status_code=404, detail="Rate plan not found")
        plan_name = plan["name"]
        rows = _apply_plan(rows, plan)

    return CalendarOut(
        date_from=date_from, date_to=date_to, dates=dates, rows=rows,
        forecast=forecast, rate_plan_id=rate_plan_id, rate_plan_name=plan_name,
        editable=rate_plan_id is None,
    )


def _apply_plan(rows: list[CalendarRow], plan) -> list[CalendarRow]:
    """Re-price each cell as the plan would sell it.

    A plan with a flat rate quotes that price on every date. Otherwise the
    room type's effective rate for the date carries the plan's adjustment —
    the same arithmetic the plan's headline "from" price uses, so the two can
    never disagree.
    """
    covered = set(plan["room_type_ids"] or [])
    flat = plan["flat_rate"]
    out: list[CalendarRow] = []
    for row in rows:
        # No links means the plan covers every room type.
        if covered and row.room_type_id not in covered:
            continue
        days = []
        for cell in row.days:
            if flat is not None:
                priced = flat
            elif cell.rate is None:
                priced = None
            else:
                signed = (
                    -plan["adjustment_value"]
                    if plan["adjustment_direction"] == "decrease"
                    else plan["adjustment_value"]
                )
                priced = (
                    cell.rate * (1 + signed / Decimal(100))
                    if plan["adjustment_type"] == "percent"
                    else cell.rate + signed
                )
                priced = max(Decimal(0), _money(priced))
            days.append(cell.model_copy(update={
                "rate": priced,
                # The figure is the plan's, not something set on this date.
                "rate_is_override": False,
            }))
        out.append(row.model_copy(update={"days": days}))
    return out


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
def _upsert(
    db: Session, *, caller: Caller, property_id: uuid.UUID, room_type_id: uuid.UUID,
    stay_date: date, sets: dict[str, Any],
) -> None:
    """Write one override cell, creating the row only when needed."""
    assignments = ", ".join(f"{col} = :{col}" for col in sets)
    db.execute(
        text(
            f"""
            INSERT INTO property.rate_calendar_days
                (organization_id, property_id, room_type_id, stay_date,
                 rate, min_stay, stop_sell, updated_by)
            VALUES (:org, :prop, :rt, :d,
                    :ins_rate, :ins_min_stay, :ins_stop_sell, :actor)
            ON CONFLICT (property_id, room_type_id, stay_date) DO UPDATE
               SET {assignments}, updated_at = now(), updated_by = :actor,
                   version = property.rate_calendar_days.version + 1
            """
        ),
        {
            "org": caller.organization_id, "prop": property_id, "rt": room_type_id,
            "d": stay_date, "actor": caller.user_id,
            "ins_rate": sets.get("rate"),
            "ins_min_stay": sets.get("min_stay"),
            "ins_stop_sell": sets.get("stop_sell", False),
            **sets,
        },
    )
    # The table means "overrides only" (migration 0011), so a row where
    # everything has been cleared is indistinguishable from no row at all.
    db.execute(
        text(
            """
            DELETE FROM property.rate_calendar_days
             WHERE property_id = :prop AND room_type_id = :rt AND stay_date = :d
               AND rate IS NULL AND min_stay IS NULL AND NOT stop_sell
            """
        ),
        {"prop": property_id, "rt": room_type_id, "d": stay_date},
    )


@calendar_router.put("/rate-calendar/cell", response_model=CalendarCell)
def update_cell(
    property_id: uuid.UUID,
    body: CellUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """One inline edit. Clearing a value restores the room type's own figure."""
    assert_property_in_org(db, caller, property_id)
    owns = db.execute(
        text(
            "SELECT 1 FROM property.room_types WHERE id = :id AND property_id = :prop"
        ),
        {"id": body.room_type_id, "prop": property_id},
    ).first()
    if owns is None:
        raise HTTPException(status_code=404, detail="Room type not found")
    if body.rate is not None and body.rate < 0:
        raise HTTPException(status_code=422, detail="Rate cannot be negative")
    if body.min_stay is not None and body.min_stay < 1:
        raise HTTPException(status_code=422, detail="Minimum stay must be at least 1 night")

    sets: dict[str, Any] = {}
    if body.clear_rate:
        sets["rate"] = None
    elif body.rate is not None:
        sets["rate"] = body.rate
    if body.clear_min_stay:
        sets["min_stay"] = None
    elif body.min_stay is not None:
        sets["min_stay"] = body.min_stay
    if body.stop_sell is not None:
        sets["stop_sell"] = body.stop_sell
    if not sets:
        raise HTTPException(status_code=422, detail="Nothing to change")

    _upsert(
        db, caller=caller, property_id=property_id, room_type_id=body.room_type_id,
        stay_date=body.stay_date, sets=sets,
    )
    record_audit(
        db, action="rate_calendar.cell.update", entity_type="rate_calendar_day",
        entity_id=f"{body.room_type_id}:{body.stay_date}",
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={k: (str(v) if isinstance(v, Decimal) else v) for k, v in sets.items()},
    )
    grid = rate_calendar(
        property_id, body.stay_date, body.stay_date, body.room_type_id,
        None, caller, db,
    )
    return grid.rows[0].days[0]


@calendar_router.post("/rate-calendar/bulk", response_model=BulkUpdateOut)
def bulk_update(
    property_id: uuid.UUID,
    body: BulkUpdateIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """The right-hand panel. ``preview_only`` runs everything but the write."""
    assert_property_in_org(db, caller, property_id)
    _validate(body.change, CHANGE_MODES, "change")
    _validate(body.value_type, VALUE_TYPES, "value_type")
    if body.value < 0:
        raise HTTPException(status_code=422, detail="Value cannot be negative")
    if body.change == "decrease" and body.value_type == "percent" and body.value > 100:
        raise HTTPException(
            status_code=422, detail="A percentage decrease cannot be more than 100%"
        )
    if body.update_min_stay and (body.min_stay is None or body.min_stay < 1):
        raise HTTPException(
            status_code=422, detail="Minimum stay must be at least 1 night"
        )
    if body.update_stop_sell and body.stop_sell is None:
        raise HTTPException(status_code=422, detail="Choose a stop sell value to apply")
    if len(body.dates) > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=422, detail=f"Select at most {MAX_WINDOW_DAYS} dates"
        )

    types = _room_types(db, property_id, body.room_type_ids or None)
    if not types:
        raise HTTPException(
            status_code=422, detail="No active room types match this selection"
        )
    known = {t["id"] for t in types}
    missing = set(body.room_type_ids) - known
    if missing:
        raise HTTPException(
            status_code=422,
            detail="One or more room types are not active in this property",
        )

    span = sorted(body.dates)
    grid = rate_calendar(property_id, span[0], span[-1], None, None, caller, db)
    by_type = {r.room_type_id: r for r in grid.rows}
    wanted = set(span)

    preview: list[BulkPreviewRow] = []
    cell_count = 0
    for rt in types:
        row = by_type.get(rt["id"])
        cells = [c for c in (row.days if row else []) if c.stay_date in wanted]
        currents = [c.rate for c in cells if c.rate is not None]
        news: list[Decimal] = []
        for cell in cells:
            current = cell.rate if cell.rate is not None else Decimal(0)
            new = _new_rate(current, body)
            news.append(new)
            cell_count += 1
            if not body.preview_only:
                sets: dict[str, Any] = {"rate": new}
                if body.update_min_stay:
                    sets["min_stay"] = body.min_stay
                if body.update_stop_sell:
                    sets["stop_sell"] = body.stop_sell
                _upsert(
                    db, caller=caller, property_id=property_id, room_type_id=rt["id"],
                    stay_date=cell.stay_date, sets=sets,
                )
        preview.append(
            BulkPreviewRow(
                room_type_id=rt["id"], room_type_name=rt["name"],
                current_avg=_money(sum(currents) / len(currents)) if currents else None,
                new_avg=_money(sum(news) / len(news)) if news else None,
            )
        )

    if not body.preview_only:
        record_audit(
            db, action="rate_calendar.bulk_update", entity_type="rate_calendar",
            entity_id=str(property_id), organization_id=caller.organization_id,
            property_id=property_id, actor_subject=caller.subject,
            after={
                "change": body.change, "value_type": body.value_type,
                "value": str(body.value), "dates": [str(d) for d in span],
                "room_types": [t["name"] for t in types],
                "cells": cell_count,
                "min_stay": body.min_stay if body.update_min_stay else None,
                "stop_sell": body.stop_sell if body.update_stop_sell else None,
            },
            reason=body.reason,
        )

    return BulkUpdateOut(
        rows=preview, room_type_count=len(types), date_count=len(span),
        cell_count=cell_count, applied=not body.preview_only,
    )


@calendar_router.post("/rate-calendar/copy", response_model=CopyRatesOut)
def copy_rates(
    property_id: uuid.UUID,
    body: CopyRatesIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Copy a window's rates forward, day by day, in order."""
    assert_property_in_org(db, caller, property_id)
    source = _window(body.source_from, body.source_to)
    target_to = body.target_from + timedelta(days=len(source) - 1)
    if body.target_from <= body.source_to and target_to >= body.source_from:
        raise HTTPException(
            status_code=422,
            detail="The target range overlaps the source range. Choose dates outside it.",
        )
    types = _room_types(db, property_id, body.room_type_ids or None)
    if not types:
        raise HTTPException(
            status_code=422, detail="No active room types match this selection"
        )

    grid = rate_calendar(property_id, source[0], source[-1], None, None, caller, db)
    by_type = {r.room_type_id: r for r in grid.rows}
    cell_count = 0
    for rt in types:
        row = by_type.get(rt["id"])
        for offset, cell in enumerate(row.days if row else []):
            target_date = body.target_from + timedelta(days=offset)
            cell_count += 1
            if body.preview_only:
                continue
            sets: dict[str, Any] = {"rate": cell.rate}
            if body.include_restrictions:
                sets["min_stay"] = cell.min_stay if cell.min_stay_is_override else None
                sets["stop_sell"] = cell.stop_sell
            _upsert(
                db, caller=caller, property_id=property_id, room_type_id=rt["id"],
                stay_date=target_date, sets=sets,
            )

    if not body.preview_only:
        record_audit(
            db, action="rate_calendar.copy", entity_type="rate_calendar",
            entity_id=str(property_id), organization_id=caller.organization_id,
            property_id=property_id, actor_subject=caller.subject,
            after={
                "source": f"{body.source_from} to {body.source_to}",
                "target": f"{body.target_from} to {target_to}",
                "room_types": [t["name"] for t in types], "cells": cell_count,
                "include_restrictions": body.include_restrictions,
            },
        )
    return CopyRatesOut(
        days=len(source), room_type_count=len(types), cell_count=cell_count,
        applied=not body.preview_only, target_to=target_to,
    )
