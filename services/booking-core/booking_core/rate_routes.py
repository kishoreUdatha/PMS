"""Rate Plans API (screen 033).

A rate plan sells the same physical room on different terms. It holds an
*adjustment* to the room type's base rate rather than a price of its own, so
the "from" figure the screen shows is computed on read and a change to a room
type's base rate moves every plan built on it. There is no second copy of the
price to go stale.

Corporate rates are not a separate entity: they are rate plans with
``plan_type='corporate'`` and an account name, which is why the mockup lists
"Corporate BlueWave" among the ordinary plans and again under its own tab.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.objectstore import (
    ObjectStoreError,
    build_key,
    delete_object,
    put_object,
)
from chirala_common.routing import TransactionalRoute
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_session
from .rooms_routes import _STORE, _read_upload, photo_url
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

rate_router = APIRouter(tags=["rate-plans"], route_class=TransactionalRoute)

PLAN_TYPES = ("standard", "corporate", "package")
ADJUSTMENT_DIRECTIONS = ("increase", "decrease")
ADJUSTMENT_TYPES = ("percent", "amount")
STATUSES = ("active", "inactive")

PLAN_TYPE_LABELS = {
    "standard": "Standard", "corporate": "Corporate", "package": "Package",
}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class MealPlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    display_order: int
    status: str
    rate_plan_count: int = 0
    version: int


class MealPlanIn(BaseModel):
    code: str = Field(min_length=1, max_length=10)
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    display_order: int = 0
    status: str = "active"


class MealPlanUpdate(MealPlanIn):
    version: int


class RatePlanIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    plan_type: str = "standard"
    #: Which company this rate was negotiated with. An id, so a booking billed
    #: to that account can find its rate; the free-text name it replaced could
    #: not be matched against anything.
    commercial_account_id: uuid.UUID | None = None
    corporate_account: str | None = Field(default=None, max_length=120)
    meal_plan_id: uuid.UUID | None = None
    adjustment_direction: str = "increase"
    adjustment_type: str = "percent"
    adjustment_value: Decimal = Decimal("0")
    # Set means "this is the nightly price"; None means follow the room type.
    flat_rate: Decimal | None = None
    extra_adult_charge: Decimal | None = None
    child_charge: Decimal | None = None
    min_stay: int = 1
    min_guests: int = 1
    max_guests: int = 2
    refundable: bool = True
    free_cancellation_hours: int | None = None
    cancellation_policy: str | None = Field(default=None, max_length=300)
    status: str = "active"
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)
    reason: str | None = None


class RatePlanUpdate(RatePlanIn):
    version: int


class RatePlanStatusIn(BaseModel):
    status: str
    version: int
    reason: str | None = None


class RatePlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    plan_type: str
    plan_type_label: str
    commercial_account_id: uuid.UUID | None = None
    #: The account's own name, so a screen never has to fetch the list to
    #: render one label.
    commercial_account_name: str | None = None
    corporate_account: str | None = None
    meal_plan_id: uuid.UUID | None = None
    meal_plan_code: str | None = None
    meal_plan_name: str | None = None
    adjustment_direction: str
    adjustment_type: str
    adjustment_value: Decimal
    flat_rate: Decimal | None = None
    extra_adult_charge: Decimal | None = None
    child_charge: Decimal | None = None
    min_stay: int
    min_guests: int
    max_guests: int
    refundable: bool
    free_cancellation_hours: int | None = None
    cancellation_policy: str | None = None
    cancellation_label: str
    status: str
    image_url: str | None = None
    from_rate: Decimal | None = None
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)
    applies_to_all_room_types: bool = True
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by_name: str | None = None
    updated_by_name: str | None = None


class RatePlanStatsOut(BaseModel):
    """Counts behind the list header."""

    total: int
    active: int
    inactive: int
    corporate: int


class RatePlanListOut(BaseModel):
    items: list[RatePlanOut]
    stats: RatePlanStatsOut


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _validate(value: str | None, allowed: tuple[str, ...], field: str) -> None:
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


def _check_plan(body: RatePlanIn) -> None:
    """Reject the combinations the screen should never submit.

    The same rules exist as CHECK constraints; these run first so the user gets
    a sentence about the field rather than a constraint name.
    """
    _validate(body.plan_type, PLAN_TYPES, "plan_type")
    _validate(body.status, STATUSES, "status")
    _validate(body.adjustment_direction, ADJUSTMENT_DIRECTIONS, "adjustment_direction")
    _validate(body.adjustment_type, ADJUSTMENT_TYPES, "adjustment_type")
    if body.flat_rate is not None and body.flat_rate < 0:
        raise HTTPException(status_code=422, detail="Price cannot be negative")
    if body.adjustment_value < 0:
        raise HTTPException(status_code=422, detail="Adjustment cannot be negative")
    if (
        body.adjustment_type == "percent"
        and body.adjustment_direction == "decrease"
        and body.adjustment_value > 100
    ):
        raise HTTPException(
            status_code=422,
            detail="A percentage discount cannot be more than 100%",
        )
    if body.min_stay < 1:
        raise HTTPException(status_code=422, detail="Minimum stay must be at least 1 night")
    if body.min_guests < 1 or body.max_guests < body.min_guests:
        raise HTTPException(
            status_code=422, detail="Guest range must start at 1 and end no lower than it"
        )
    if not body.refundable and body.free_cancellation_hours is not None:
        raise HTTPException(
            status_code=422,
            detail="A non-refundable plan cannot offer free cancellation hours",
        )
    if body.plan_type == "corporate" and body.commercial_account_id is None             and not (body.corporate_account or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A corporate plan needs the company it was negotiated with.",
        )
    for label, amount in (
        ("Extra adult charge", body.extra_adult_charge),
        ("Child charge", body.child_charge),
    ):
        if amount is not None and amount < 0:
            raise HTTPException(status_code=422, detail=f"{label} cannot be negative")


def _cancellation_label(row) -> str:
    """The chip on each card."""
    if not row["refundable"]:
        return "Non-refundable"
    hours = row["free_cancellation_hours"]
    return f"Free Cancellation ({hours} hrs)" if hours else "Free Cancellation"


# The plan's price is the room type's base rate with the adjustment applied.
# Kept in SQL so the list can be sorted and filtered on it without a second
# pass, and so it can never disagree with a per-plan calculation in Python.
# The plan's price is the *effective* nightly rate with the adjustment applied,
# lowest across the room types it covers over the next 90 days.
#
# "Effective" matters: a date priced on the Rates & Inventory calendar overrides
# the room type's base rate, so a plan must read that too. Reading base_rate
# alone made a plan quote 7,475 on a night the calendar had priced at 9,000.
#
# LEAST ignores NULLs, so the two candidates are: the base rate (only when some
# date in the window is still un-overridden) and the cheapest override present.
_EFFECTIVE_RATE_SQL = """
    LEAST(
        CASE WHEN (SELECT count(*) FROM property.rate_calendar_days c
                    WHERE c.room_type_id = rt.id
                      AND c.rate IS NOT NULL
                      AND c.stay_date BETWEEN CURRENT_DATE
                                          AND CURRENT_DATE + 89) < 90
             THEN rt.base_rate END,
        (SELECT min(c.rate) FROM property.rate_calendar_days c
          WHERE c.room_type_id = rt.id
            AND c.rate IS NOT NULL
            AND c.stay_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 89)
    )
"""

_FROM_RATE_SQL = f"""
    COALESCE(rp.flat_rate,
    (SELECT round(min(
         GREATEST(
             CASE
                 WHEN rp.adjustment_type = 'percent' THEN
                     {_EFFECTIVE_RATE_SQL} * (1 + (CASE WHEN rp.adjustment_direction = 'decrease'
                                           THEN -rp.adjustment_value
                                           ELSE rp.adjustment_value END) / 100.0)
                 ELSE
                     {_EFFECTIVE_RATE_SQL} + (CASE WHEN rp.adjustment_direction = 'decrease'
                                      THEN -rp.adjustment_value
                                      ELSE rp.adjustment_value END)
             END,
             0
         )
     ), 2)
     FROM property.room_types rt
     WHERE rt.property_id = rp.property_id
       AND rt.status = 'active'
       AND rt.base_rate IS NOT NULL
       AND (NOT EXISTS (SELECT 1 FROM property.rate_plan_room_types l
                         WHERE l.rate_plan_id = rp.id)
            OR EXISTS (SELECT 1 FROM property.rate_plan_room_types l
                        WHERE l.rate_plan_id = rp.id AND l.room_type_id = rt.id))
    ))
"""

_PLAN_SELECT = f"""
    SELECT rp.id, rp.code, rp.name, rp.description, rp.plan_type,
           rp.corporate_account, rp.commercial_account_id,
           ca.name AS commercial_account_name, rp.meal_plan_id,
           mp.code AS meal_plan_code, mp.name AS meal_plan_name,
           rp.adjustment_direction, rp.adjustment_type, rp.adjustment_value,
           rp.flat_rate,
           rp.extra_adult_charge, rp.child_charge, rp.min_stay,
           rp.min_guests, rp.max_guests, rp.refundable,
           rp.free_cancellation_hours, rp.cancellation_policy, rp.status,
           rp.image_url, rp.image_storage_key, rp.version,
           rp.created_at, rp.updated_at,
           cu.display_name AS created_by_name,
           uu.display_name AS updated_by_name,
           {_FROM_RATE_SQL} AS from_rate,
           ARRAY(SELECT l.room_type_id FROM property.rate_plan_room_types l
                  WHERE l.rate_plan_id = rp.id) AS room_type_ids
    FROM property.rate_plans rp
    LEFT JOIN property.meal_plans mp ON mp.id = rp.meal_plan_id
    LEFT JOIN engagement.commercial_accounts ca
           ON ca.id = rp.commercial_account_id
    LEFT JOIN iam.users cu ON cu.id = rp.created_by
    LEFT JOIN iam.users uu ON uu.id = rp.updated_by
"""


def _plan_out(row) -> RatePlanOut:
    data = dict(row)
    key = data.pop("image_storage_key", None)
    stored_url = data.pop("image_url", None)
    room_type_ids = list(data.pop("room_type_ids") or [])
    return RatePlanOut(
        **data,
        plan_type_label=PLAN_TYPE_LABELS.get(row["plan_type"], row["plan_type"]),
        cancellation_label=_cancellation_label(row),
        # An uploaded image is presigned; a hosted one keeps its absolute URL.
        image_url=photo_url(stored_url, key),
        room_type_ids=room_type_ids,
        applies_to_all_room_types=not room_type_ids,
    )


def _one_plan(db: Session, plan_id: uuid.UUID, property_id: uuid.UUID) -> RatePlanOut:
    row = db.execute(
        text(f"{_PLAN_SELECT} WHERE rp.id = :id AND rp.property_id = :prop"),
        {"id": plan_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Rate plan not found")
    return _plan_out(row)


def _snapshot(db: Session, plan_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT code, name, plan_type, corporate_account,
                   commercial_account_id, meal_plan_id,
                   adjustment_direction, adjustment_type, adjustment_value,
                   flat_rate, extra_adult_charge, child_charge, min_stay, min_guests,
                   max_guests, refundable, free_cancellation_hours,
                   cancellation_policy, status
            FROM property.rate_plans WHERE id = :id
            """
        ),
        {"id": plan_id},
    ).mappings().first()
    if row is None:
        return None
    return {
        k: (str(v) if isinstance(v, (Decimal, uuid.UUID)) else v)
        for k, v in dict(row).items()
    }


def _replace_room_types(
    db: Session, *, plan_id: uuid.UUID, property_id: uuid.UUID,
    room_type_ids: list[uuid.UUID],
) -> None:
    """Empty list means "all room types" — see migration 0010."""
    db.execute(
        text("DELETE FROM property.rate_plan_room_types WHERE rate_plan_id = :id"),
        {"id": plan_id},
    )
    if not room_type_ids:
        return
    owned = db.execute(
        text(
            "SELECT id FROM property.room_types "
            "WHERE property_id = :prop AND id = ANY(:ids)"
        ),
        {"prop": property_id, "ids": room_type_ids},
    ).scalars().all()
    if len(set(owned)) != len(set(room_type_ids)):
        raise HTTPException(
            status_code=422, detail="One or more room types are not in this property"
        )
    db.execute(
        text(
            """
            INSERT INTO property.rate_plan_room_types
                (rate_plan_id, room_type_id, property_id)
            SELECT :id, unnest(CAST(:ids AS uuid[])), :prop
            """
        ),
        {"id": plan_id, "ids": room_type_ids, "prop": property_id},
    )


def _check_meal_plan(db: Session, meal_plan_id: uuid.UUID | None, property_id) -> None:
    if meal_plan_id is None:
        return
    owned = db.execute(
        text(
            "SELECT 1 FROM property.meal_plans "
            "WHERE id = :id AND property_id = :prop AND status = 'active'"
        ),
        {"id": meal_plan_id, "prop": property_id},
    ).first()
    if owned is None:
        raise HTTPException(
            status_code=422, detail="Meal plan is not an active plan in this property"
        )


# --------------------------------------------------------------------------
# Meal plans
# --------------------------------------------------------------------------
@rate_router.get("/meal-plans", response_model=list[MealPlanOut])
def list_meal_plans(
    property_id: uuid.UUID,
    include_inactive: bool = True,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    where = ["mp.property_id = :prop"]
    if not include_inactive:
        where.append("mp.status = 'active'")
    rows = db.execute(
        text(
            f"""
            SELECT mp.id, mp.code, mp.name, mp.description, mp.display_order,
                   mp.status, mp.version,
                   (SELECT count(*) FROM property.rate_plans rp
                     WHERE rp.meal_plan_id = mp.id) AS rate_plan_count
            FROM property.meal_plans mp
            WHERE {' AND '.join(where)}
            ORDER BY mp.display_order, mp.name
            """
        ),
        {"prop": property_id},
    ).mappings().all()
    return [MealPlanOut(**r) for r in rows]


@rate_router.post(
    "/meal-plans", response_model=MealPlanOut, status_code=status.HTTP_201_CREATED
)
def create_meal_plan(
    body: MealPlanIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate(body.status, STATUSES, "status")
    meal_plan_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.meal_plans
                    (id, organization_id, property_id, code, name, description,
                     display_order, status, created_by, updated_by)
                VALUES (:id, :org, :prop, :code, :name, :description,
                        :display_order, :status, :actor, :actor)
                """
            ),
            {
                "id": meal_plan_id, "org": caller.organization_id, "prop": property_id,
                "code": body.code.strip().upper(), "name": body.name.strip(),
                "description": body.description, "display_order": body.display_order,
                "status": body.status, "actor": caller.user_id,
            },
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Meal plan code {body.code!r} already exists"
        ) from exc
    record_audit(
        db, action="meal_plan.create", entity_type="meal_plan",
        entity_id=str(meal_plan_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"code": body.code, "name": body.name},
    )
    return [
        m for m in list_meal_plans(property_id, True, caller, db) if m.id == meal_plan_id
    ][0]


@rate_router.put("/meal-plans/{meal_plan_id}", response_model=MealPlanOut)
def update_meal_plan(
    meal_plan_id: uuid.UUID,
    property_id: uuid.UUID,
    body: MealPlanUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate(body.status, STATUSES, "status")
    if body.status == "inactive":
        in_use = db.execute(
            text(
                "SELECT count(*) FROM property.rate_plans "
                "WHERE meal_plan_id = :id AND status = 'active'"
            ),
            {"id": meal_plan_id},
        ).scalar_one()
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=f"{in_use} active rate plan(s) use this meal plan. "
                       f"Move them to another meal plan first.",
            )
    try:
        result = db.execute(
            text(
                """
                UPDATE property.meal_plans
                   SET code = :code, name = :name, description = :description,
                       display_order = :display_order, status = :status,
                       updated_at = now(), updated_by = :actor,
                       version = version + 1
                 WHERE id = :id AND property_id = :prop AND version = :version
                """
            ),
            {
                "id": meal_plan_id, "prop": property_id,
                "code": body.code.strip().upper(), "name": body.name.strip(),
                "description": body.description, "display_order": body.display_order,
                "status": body.status, "actor": caller.user_id, "version": body.version,
            },
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Meal plan code {body.code!r} already exists"
        ) from exc
    if result.rowcount == 0:
        raise _conflict("Meal plan")
    record_audit(
        db, action="meal_plan.update", entity_type="meal_plan",
        entity_id=str(meal_plan_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"code": body.code, "name": body.name, "status": body.status},
    )
    return [
        m for m in list_meal_plans(property_id, True, caller, db) if m.id == meal_plan_id
    ][0]


# --------------------------------------------------------------------------
# Rate plans
# --------------------------------------------------------------------------
@rate_router.get("/rate-plans", response_model=RatePlanListOut)
def list_rate_plans(
    property_id: uuid.UUID,
    search: str | None = None,
    plan_status: str | None = Query(default=None, alias="status"),
    meal_plan_id: uuid.UUID | None = None,
    room_type_id: uuid.UUID | None = None,
    plan_type: str | None = None,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The list behind the Rate Plans tab, with the screen's four filters."""
    assert_property_in_org(db, caller, property_id)
    _validate(plan_status, STATUSES, "status")
    _validate(plan_type, PLAN_TYPES, "plan_type")

    where = ["rp.property_id = :prop"]
    params: dict[str, Any] = {"prop": property_id}
    if search:
        where.append(
            "(rp.name ILIKE :q OR rp.code ILIKE :q OR rp.description ILIKE :q"
            " OR rp.corporate_account ILIKE :q)"
        )
        params["q"] = f"%{search}%"
    if plan_status:
        where.append("rp.status = :status")
        params["status"] = plan_status
    if meal_plan_id:
        where.append("rp.meal_plan_id = :meal")
        params["meal"] = meal_plan_id
    if plan_type:
        where.append("rp.plan_type = :ptype")
        params["ptype"] = plan_type
    if room_type_id:
        # A plan with no links applies to every room type, so it matches too.
        where.append(
            """(NOT EXISTS (SELECT 1 FROM property.rate_plan_room_types l
                             WHERE l.rate_plan_id = rp.id)
                OR EXISTS (SELECT 1 FROM property.rate_plan_room_types l
                            WHERE l.rate_plan_id = rp.id
                              AND l.room_type_id = :rtid))"""
        )
        params["rtid"] = room_type_id

    rows = db.execute(
        text(f"{_PLAN_SELECT} WHERE {' AND '.join(where)} "
             "ORDER BY rp.status, rp.name"),
        params,
    ).mappings().all()

    counts = db.execute(
        text(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'active')     AS active,
                   count(*) FILTER (WHERE status = 'inactive')   AS inactive,
                   count(*) FILTER (WHERE plan_type = 'corporate') AS corporate
            FROM property.rate_plans WHERE property_id = :prop
            """
        ),
        {"prop": property_id},
    ).mappings().first()

    return RatePlanListOut(
        items=[_plan_out(r) for r in rows], stats=RatePlanStatsOut(**counts)
    )


# Declared before /rate-plans/{plan_id}: FastAPI matches in order, and a
# path parameter happily swallows a literal segment -- 'for-account' was
# being parsed as a plan id and answering 422.
@rate_router.get("/rate-plans/for-account", response_model=list[RatePlanOut])
def plans_for_account(
    property_id: uuid.UUID,
    commercial_account_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """The rates negotiated with one company.

    Exists so taking a booking can offer the right rate instead of relying on
    whoever is at the desk remembering that a rate was agreed. Before this,
    the company was chosen for *billing* and the rate was picked from the
    whole list, with nothing joining the two -- the negotiated rate applied
    only when somebody happened to recall it.

    Active plans only: a paused or retired agreement is history, and quoting
    from it would honour a rate the property has stopped offering.
    """
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            f"""
            {_PLAN_SELECT}
            WHERE rp.property_id = :prop
              AND rp.plan_type = 'corporate'
              AND rp.commercial_account_id = :account
              AND rp.status = 'active'
            ORDER BY rp.name
            """
        ),
        {"prop": property_id, "account": commercial_account_id},
    ).mappings().all()
    return [_plan_out(r) for r in rows]


@rate_router.get("/rate-plans/{plan_id}", response_model=RatePlanOut)
def get_rate_plan(
    plan_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    return _one_plan(db, plan_id, property_id)


@rate_router.post(
    "/rate-plans", response_model=RatePlanOut, status_code=status.HTTP_201_CREATED
)
def create_rate_plan(
    body: RatePlanIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_plan(body)
    _check_meal_plan(db, body.meal_plan_id, property_id)

    plan_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.rate_plans
                    (id, organization_id, property_id, code, name, description,
                     plan_type, corporate_account, commercial_account_id,
                     meal_plan_id,
                     adjustment_direction, adjustment_type, adjustment_value,
                     flat_rate, extra_adult_charge, child_charge, min_stay,
                     min_guests, max_guests, refundable, free_cancellation_hours,
                     cancellation_policy, status, created_by, updated_by)
                VALUES (:id, :org, :prop, :code, :name, :description, :plan_type,
                        :corporate_account, :commercial_account_id,
                        :meal_plan_id, :adjustment_direction,
                        :adjustment_type, :adjustment_value, :flat_rate,
                        :extra_adult_charge,
                        :child_charge, :min_stay, :min_guests, :max_guests,
                        :refundable, :free_cancellation_hours,
                        :cancellation_policy, :status, :actor, :actor)
                """
            ),
            _plan_params(body, plan_id, property_id, caller),
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Rate plan code {body.code!r} already exists"
        ) from exc

    _replace_room_types(
        db, plan_id=plan_id, property_id=property_id, room_type_ids=body.room_type_ids
    )
    record_audit(
        db, action="rate_plan.create", entity_type="rate_plan", entity_id=str(plan_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after=_snapshot(db, plan_id), reason=body.reason,
    )
    return _one_plan(db, plan_id, property_id)


def _plan_params(
    body: RatePlanIn, plan_id: uuid.UUID, property_id: uuid.UUID, caller: Caller
) -> dict[str, Any]:
    return {
        "id": plan_id, "org": caller.organization_id, "prop": property_id,
        "code": body.code.strip().upper(), "name": body.name.strip(),
        "description": body.description, "plan_type": body.plan_type,
        "corporate_account": (body.corporate_account or "").strip() or None,
        "commercial_account_id": body.commercial_account_id,
        "meal_plan_id": body.meal_plan_id,
        "adjustment_direction": body.adjustment_direction,
        "adjustment_type": body.adjustment_type,
        "adjustment_value": body.adjustment_value,
        "flat_rate": body.flat_rate,
        "extra_adult_charge": body.extra_adult_charge,
        "child_charge": body.child_charge, "min_stay": body.min_stay,
        "min_guests": body.min_guests, "max_guests": body.max_guests,
        "refundable": body.refundable,
        "free_cancellation_hours": body.free_cancellation_hours,
        "cancellation_policy": body.cancellation_policy, "status": body.status,
        "actor": caller.user_id,
    }


@rate_router.put("/rate-plans/{plan_id}", response_model=RatePlanOut)
def update_rate_plan(
    plan_id: uuid.UUID,
    property_id: uuid.UUID,
    body: RatePlanUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_plan(body)
    _check_meal_plan(db, body.meal_plan_id, property_id)
    before = _snapshot(db, plan_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Rate plan not found")

    params = _plan_params(body, plan_id, property_id, caller)
    params["version"] = body.version
    try:
        result = db.execute(
            text(
                """
                UPDATE property.rate_plans
                   SET code = :code, name = :name, description = :description,
                       plan_type = :plan_type, corporate_account = :corporate_account,
                       commercial_account_id = :commercial_account_id,
                       meal_plan_id = :meal_plan_id,
                       adjustment_direction = :adjustment_direction,
                       adjustment_type = :adjustment_type,
                       adjustment_value = :adjustment_value,
                       flat_rate = :flat_rate,
                       extra_adult_charge = :extra_adult_charge,
                       child_charge = :child_charge, min_stay = :min_stay,
                       min_guests = :min_guests, max_guests = :max_guests,
                       refundable = :refundable,
                       free_cancellation_hours = :free_cancellation_hours,
                       cancellation_policy = :cancellation_policy,
                       status = :status, updated_at = now(), updated_by = :actor,
                       version = version + 1
                 WHERE id = :id AND property_id = :prop AND version = :version
                """
            ),
            params,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Rate plan code {body.code!r} already exists"
        ) from exc
    if result.rowcount == 0:
        raise _conflict("Rate plan")

    _replace_room_types(
        db, plan_id=plan_id, property_id=property_id, room_type_ids=body.room_type_ids
    )
    record_audit(
        db, action="rate_plan.update", entity_type="rate_plan", entity_id=str(plan_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before=before, after=_snapshot(db, plan_id),
        reason=body.reason,
    )
    return _one_plan(db, plan_id, property_id)


@rate_router.post("/rate-plans/{plan_id}/status", response_model=RatePlanOut)
def set_rate_plan_status(
    plan_id: uuid.UUID,
    property_id: uuid.UUID,
    body: RatePlanStatusIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Activate / deactivate. Deactivating is how a plan is retired — it is
    never deleted, because past reservations were sold on its terms."""
    assert_property_in_org(db, caller, property_id)
    _validate(body.status, STATUSES, "status")
    before = _snapshot(db, plan_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Rate plan not found")

    result = db.execute(
        text(
            """
            UPDATE property.rate_plans
               SET status = :status, updated_at = now(), updated_by = :actor,
                   version = version + 1
             WHERE id = :id AND property_id = :prop AND version = :version
            """
        ),
        {
            "status": body.status, "id": plan_id, "prop": property_id,
            "actor": caller.user_id, "version": body.version,
        },
    )
    if result.rowcount == 0:
        raise _conflict("Rate plan")
    record_audit(
        db,
        action="rate_plan.activate" if body.status == "active" else "rate_plan.deactivate",
        entity_type="rate_plan", entity_id=str(plan_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before=before, after=_snapshot(db, plan_id),
        reason=body.reason,
    )
    return _one_plan(db, plan_id, property_id)


@rate_router.post(
    "/rate-plans/{plan_id}/duplicate", response_model=RatePlanOut,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_rate_plan(
    plan_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    """The Duplicate button. The copy starts inactive so it cannot be sold
    before someone has looked at it."""
    assert_property_in_org(db, caller, property_id)
    source = db.execute(
        text(
            "SELECT * FROM property.rate_plans WHERE id = :id AND property_id = :prop"
        ),
        {"id": plan_id, "prop": property_id},
    ).mappings().first()
    if source is None:
        raise HTTPException(status_code=404, detail="Rate plan not found")

    # First free "<CODE>-2", "-3", … so duplicating twice does not collide.
    taken = set(
        db.execute(
            text("SELECT code FROM property.rate_plans WHERE property_id = :prop"),
            {"prop": property_id},
        ).scalars().all()
    )
    suffix = 2
    while f"{source['code'][:10]}-{suffix}" in taken:
        suffix += 1
    new_code = f"{source['code'][:10]}-{suffix}"

    new_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO property.rate_plans
                (id, organization_id, property_id, code, name, description,
                 plan_type, corporate_account, meal_plan_id, adjustment_direction,
                 adjustment_type, adjustment_value, flat_rate, extra_adult_charge,
                 child_charge, min_stay, min_guests, max_guests, refundable,
                 free_cancellation_hours, cancellation_policy, image_url,
                 status, created_by, updated_by)
            SELECT :new_id, organization_id, property_id, :code, :name, description,
                   plan_type, corporate_account, meal_plan_id, adjustment_direction,
                   adjustment_type, adjustment_value, flat_rate, extra_adult_charge,
                   child_charge, min_stay, min_guests, max_guests, refundable,
                   free_cancellation_hours, cancellation_policy, image_url,
                   'inactive', :actor, :actor
            FROM property.rate_plans WHERE id = :id
            """
        ),
        {
            "new_id": new_id, "code": new_code,
            "name": f"{source['name']} (Copy)"[:120],
            "id": plan_id, "actor": caller.user_id,
        },
    )
    db.execute(
        text(
            """
            INSERT INTO property.rate_plan_room_types
                (rate_plan_id, room_type_id, property_id)
            SELECT :new_id, room_type_id, property_id
            FROM property.rate_plan_room_types WHERE rate_plan_id = :id
            """
        ),
        {"new_id": new_id, "id": plan_id},
    )
    record_audit(
        db, action="rate_plan.duplicate", entity_type="rate_plan",
        entity_id=str(new_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"copied_from": str(plan_id), "code": new_code},
    )
    return _one_plan(db, new_id, property_id)


@rate_router.post("/rate-plans/{plan_id}/image", response_model=RatePlanOut)
async def upload_rate_plan_image(
    plan_id: uuid.UUID,
    property_id: uuid.UUID,
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """One image per plan — the card thumbnail on screen 033."""
    assert_property_in_org(db, caller, property_id)
    existing = db.execute(
        text(
            "SELECT image_storage_key FROM property.rate_plans "
            "WHERE id = :id AND property_id = :prop"
        ),
        {"id": plan_id, "prop": property_id},
    ).mappings().first()
    if existing is None:
        raise HTTPException(status_code=404, detail="Rate plan not found")

    content, content_type = await _read_upload(file)
    key = build_key(
        "rate-plans", str(property_id), str(plan_id), content_type=content_type
    )
    try:
        put_object(_STORE, key, content, content_type)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.execute(
        text(
            """
            UPDATE property.rate_plans
               SET image_storage_key = :key, image_url = NULL,
                   updated_at = now(), updated_by = :actor, version = version + 1
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"key": key, "id": plan_id, "prop": property_id, "actor": caller.user_id},
    )
    # Only once the new key is safely recorded.
    if existing["image_storage_key"]:
        delete_object(_STORE, existing["image_storage_key"])
    record_audit(
        db, action="rate_plan.image.set", entity_type="rate_plan",
        entity_id=str(plan_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"content_type": content_type, "size_bytes": len(content)},
    )
    return _one_plan(db, plan_id, property_id)


@rate_router.delete("/rate-plans/{plan_id}/image", response_model=RatePlanOut)
def delete_rate_plan_image(
    plan_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            "SELECT image_storage_key FROM property.rate_plans "
            "WHERE id = :id AND property_id = :prop"
        ),
        {"id": plan_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Rate plan not found")
    db.execute(
        text(
            """
            UPDATE property.rate_plans
               SET image_storage_key = NULL, image_url = NULL, updated_at = now(),
                   updated_by = :actor, version = version + 1
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": plan_id, "prop": property_id, "actor": caller.user_id},
    )
    if row["image_storage_key"]:
        delete_object(_STORE, row["image_storage_key"])
    record_audit(
        db, action="rate_plan.image.delete", entity_type="rate_plan",
        entity_id=str(plan_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
    )
    return _one_plan(db, plan_id, property_id)
