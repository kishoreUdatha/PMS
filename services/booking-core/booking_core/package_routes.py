"""Packages, Add-ons and Promo Codes API (screen 035).

A package holds an *adjustment* to the room type's base rate, exactly as a rate
plan does, so its headline "from" price is computed and cannot drift from the
room type it is built on.

Bookings and revenue on a package card are **derived** from reservation units
that name the package. The booking flow does not offer packages yet, so those
figures are zero today — that is the truth, and it becomes real the moment a
reservation carries a ``package_id`` rather than requiring a backfill.

Promo redemption is the one place a counter is stored: ``usage_count`` exists so
the usage limit can be enforced under concurrency, and every increment writes a
matching row to ``promo_redemptions`` in the same transaction, so the counter
always has evidence behind it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
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
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
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

package_router = APIRouter(tags=["packages-promotions"], route_class=TransactionalRoute)

ADDON_CATEGORIES = ("dining", "spa", "transport", "activity", "room_service", "other")
PRICING_UNITS = ("per_stay", "per_night", "per_person", "per_person_per_night")
DISCOUNT_TYPES = ("percent", "amount")
STATUSES = ("active", "inactive")
DIRECTIONS = ("increase", "decrease")
ADJUSTMENT_TYPES = ("percent", "amount")

CATEGORY_LABELS = {
    "dining": "Dining", "spa": "Spa & Wellness", "transport": "Transport",
    "activity": "Activities", "room_service": "Room Service", "other": "Other",
}
UNIT_LABELS = {
    "per_stay": "Per stay", "per_night": "Per night", "per_person": "Per person",
    "per_person_per_night": "Per person, per night",
}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class AddonIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=400)
    category: str = "other"
    price: Decimal = Decimal("0")
    pricing_unit: str = "per_stay"
    status: str = "active"


class AddonUpdate(AddonIn):
    version: int


class AddonOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    category: str
    category_label: str
    price: Decimal
    pricing_unit: str
    pricing_unit_label: str
    status: str
    used_in_packages: int = 0
    version: int


class InclusionIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    addon_id: uuid.UUID | None = None


class InclusionOut(BaseModel):
    id: uuid.UUID
    label: str
    addon_id: uuid.UUID | None = None
    addon_name: str | None = None
    sort_order: int


class PackageIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    tagline: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    badge: str | None = Field(default=None, max_length=40)
    valid_from: date
    valid_to: date
    adjustment_direction: str = "increase"
    adjustment_type: str = "percent"
    adjustment_value: Decimal = Decimal("0")
    # Set means "this is the nightly price"; None means follow the room type.
    flat_rate: Decimal | None = None
    min_nights: int = 1
    max_nights: int | None = None
    is_featured: bool = True
    status: str = "active"
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)
    inclusions: list[InclusionIn] = Field(default_factory=list)
    reason: str | None = None


class PackageUpdate(PackageIn):
    version: int


class StatusIn(BaseModel):
    status: str
    version: int
    reason: str | None = None


class PackageOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    tagline: str | None = None
    description: str | None = None
    badge: str | None = None
    valid_from: date
    valid_to: date
    adjustment_direction: str
    adjustment_type: str
    adjustment_value: Decimal
    flat_rate: Decimal | None = None
    min_nights: int
    max_nights: int | None = None
    is_featured: bool
    status: str
    image_url: str | None = None
    from_rate: Decimal | None = None
    bookings: int = 0
    revenue: Decimal = Decimal("0")
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)
    applies_to_all_room_types: bool = True
    inclusions: list[InclusionOut] = Field(default_factory=list)
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PromoIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    description: str | None = Field(default=None, max_length=300)
    discount_type: str = "percent"
    discount_value: Decimal
    usage_limit: int | None = None
    valid_from: date
    valid_to: date
    min_nights: int | None = None
    package_id: uuid.UUID | None = None
    status: str = "active"
    reason: str | None = None


class PromoUpdate(PromoIn):
    version: int


class PromoOut(BaseModel):
    id: uuid.UUID
    code: str
    description: str | None = None
    discount_type: str
    discount_value: Decimal
    value_label: str
    usage_limit: int | None = None
    usage_count: int
    valid_from: date
    valid_to: date
    min_nights: int | None = None
    package_id: uuid.UUID | None = None
    package_name: str | None = None
    status: str
    state: str          # active | inactive | expired | scheduled | exhausted
    state_label: str
    version: int


class RedeemIn(BaseModel):
    reservation_id: uuid.UUID | None = None
    guest_name: str | None = Field(default=None, max_length=200)
    discount_amount: Decimal | None = None


class RedemptionOut(BaseModel):
    id: uuid.UUID
    promo_code_id: uuid.UUID
    code: str
    guest_name: str | None = None
    reservation_id: uuid.UUID | None = None
    reservation_number: str | None = None
    discount_amount: Decimal | None = None
    redeemed_by_name: str | None = None
    redeemed_at: datetime


class PromotionStats(BaseModel):
    packages: int
    active_packages: int
    promo_codes: int
    active_promo_codes: int
    redemptions: int


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


def _jsonable(value: Any) -> Any:
    if isinstance(value, (Decimal, uuid.UUID, date, datetime)):
        return str(value)
    return value


# Same shape as the rate plan calculation: the room type's base rate with the
# package's adjustment applied, lowest across the room types it is offered on.
# Same shape as the rate plan calculation, and for the same reason: a package
# must price off the *effective* nightly rate, which a date on the Rates &
# Inventory calendar can override. The window is the package's own validity
# period from today onward, so an offer quotes what it will actually sell for.
_EFFECTIVE_RATE_SQL = """
    LEAST(
        CASE WHEN (SELECT count(*) FROM property.rate_calendar_days c
                    WHERE c.room_type_id = rt.id
                      AND c.rate IS NOT NULL
                      AND c.stay_date BETWEEN GREATEST(p.valid_from, CURRENT_DATE)
                                          AND p.valid_to)
                  < (p.valid_to - GREATEST(p.valid_from, CURRENT_DATE) + 1)
             THEN rt.base_rate END,
        (SELECT min(c.rate) FROM property.rate_calendar_days c
          WHERE c.room_type_id = rt.id
            AND c.rate IS NOT NULL
            AND c.stay_date BETWEEN GREATEST(p.valid_from, CURRENT_DATE) AND p.valid_to)
    )
"""

_FROM_RATE_SQL = f"""
    COALESCE(p.flat_rate,
    (SELECT round(min(GREATEST(
         CASE
             WHEN p.adjustment_type = 'percent' THEN
                 {_EFFECTIVE_RATE_SQL} * (1 + (CASE WHEN p.adjustment_direction = 'decrease'
                                           THEN -p.adjustment_value
                                           ELSE p.adjustment_value END) / 100.0)
             ELSE
                 {_EFFECTIVE_RATE_SQL} + (CASE WHEN p.adjustment_direction = 'decrease'
                                      THEN -p.adjustment_value
                                      ELSE p.adjustment_value END)
         END, 0)), 2)
     FROM property.room_types rt
     WHERE rt.property_id = p.property_id
       AND rt.status = 'active' AND rt.base_rate IS NOT NULL
       AND (NOT EXISTS (SELECT 1 FROM property.package_room_types l
                         WHERE l.package_id = p.id)
            OR EXISTS (SELECT 1 FROM property.package_room_types l
                        WHERE l.package_id = p.id AND l.room_type_id = rt.id))))
"""

# Derived, never stored. Zero until the booking flow starts naming packages.
_BOOKINGS_SQL = """
    (SELECT count(*) FROM booking.reservation_units ru
      WHERE ru.package_id = p.id
        AND ru.status NOT IN ('cancelled', 'no_show'))
"""
_REVENUE_SQL = """
    COALESCE((SELECT sum(
                  (ru.departure_date - ru.arrival_date)
                  * COALESCE(rt2.base_rate, 0))
              FROM booking.reservation_units ru
              JOIN property.room_types rt2 ON rt2.id = ru.room_type_id
              WHERE ru.package_id = p.id
                AND ru.status NOT IN ('cancelled', 'no_show')), 0)
"""

_PACKAGE_SELECT = f"""
    SELECT p.id, p.code, p.name, p.tagline, p.description, p.badge,
           p.valid_from, p.valid_to, p.adjustment_direction, p.adjustment_type,
           p.adjustment_value, p.flat_rate, p.min_nights, p.max_nights, p.is_featured,
           p.status, p.image_url, p.image_storage_key, p.version,
           p.created_at, p.updated_at,
           {_FROM_RATE_SQL} AS from_rate,
           {_BOOKINGS_SQL}  AS bookings,
           {_REVENUE_SQL}   AS revenue,
           ARRAY(SELECT l.room_type_id FROM property.package_room_types l
                  WHERE l.package_id = p.id) AS room_type_ids
    FROM property.packages p
"""


def _inclusions(db: Session, package_ids: list[uuid.UUID]):
    if not package_ids:
        return {}
    rows = db.execute(
        text(
            """
            SELECT pi.package_id, pi.id, pi.label, pi.addon_id, pi.sort_order,
                   a.name AS addon_name
            FROM property.package_inclusions pi
            LEFT JOIN property.addons a ON a.id = pi.addon_id
            WHERE pi.package_id = ANY(:ids)
            ORDER BY pi.sort_order, pi.label
            """
        ),
        {"ids": package_ids},
    ).mappings().all()
    out: dict[uuid.UUID, list[InclusionOut]] = {}
    for r in rows:
        out.setdefault(r["package_id"], []).append(
            InclusionOut(
                id=r["id"], label=r["label"], addon_id=r["addon_id"],
                addon_name=r["addon_name"], sort_order=r["sort_order"],
            )
        )
    return out


def _package_out(row, inclusions: list[InclusionOut]) -> PackageOut:
    data = dict(row)
    key = data.pop("image_storage_key", None)
    url = data.pop("image_url", None)
    room_type_ids = list(data.pop("room_type_ids") or [])
    return PackageOut(
        **data, image_url=photo_url(url, key), room_type_ids=room_type_ids,
        applies_to_all_room_types=not room_type_ids, inclusions=inclusions,
    )


def _one_package(db: Session, package_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text(f"{_PACKAGE_SELECT} WHERE p.id = :id AND p.property_id = :prop"),
        {"id": package_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return _package_out(row, _inclusions(db, [package_id]).get(package_id, []))


def _check_package(body: PackageIn) -> None:
    _validate(body.status, STATUSES, "status")
    _validate(body.adjustment_direction, DIRECTIONS, "adjustment_direction")
    _validate(body.adjustment_type, ADJUSTMENT_TYPES, "adjustment_type")
    if body.valid_to < body.valid_from:
        raise HTTPException(status_code=422, detail="Valid-to date is before valid-from")
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
            status_code=422, detail="A percentage discount cannot be more than 100%"
        )
    if body.min_nights < 1:
        raise HTTPException(status_code=422, detail="Minimum nights must be at least 1")
    if body.max_nights is not None and body.max_nights < body.min_nights:
        raise HTTPException(
            status_code=422, detail="Maximum nights cannot be below minimum nights"
        )
    if len(body.inclusions) > 20:
        raise HTTPException(status_code=422, detail="A package can list at most 20 inclusions")


def _replace_links(
    db: Session, *, package_id: uuid.UUID, property_id: uuid.UUID,
    room_type_ids: list[uuid.UUID], inclusions: list[InclusionIn],
) -> None:
    db.execute(
        text("DELETE FROM property.package_room_types WHERE package_id = :id"),
        {"id": package_id},
    )
    if room_type_ids:
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
                INSERT INTO property.package_room_types
                    (package_id, room_type_id, property_id)
                SELECT :id, unnest(CAST(:ids AS uuid[])), :prop
                """
            ),
            {"id": package_id, "ids": room_type_ids, "prop": property_id},
        )

    db.execute(
        text("DELETE FROM property.package_inclusions WHERE package_id = :id"),
        {"id": package_id},
    )
    addon_ids = [i.addon_id for i in inclusions if i.addon_id]
    if addon_ids:
        owned = db.execute(
            text(
                "SELECT id FROM property.addons "
                "WHERE property_id = :prop AND id = ANY(:ids)"
            ),
            {"prop": property_id, "ids": addon_ids},
        ).scalars().all()
        if len(set(owned)) != len(set(addon_ids)):
            raise HTTPException(
                status_code=422, detail="One or more add-ons are not in this property"
            )
    for order, inc in enumerate(inclusions):
        db.execute(
            text(
                """
                INSERT INTO property.package_inclusions
                    (package_id, property_id, addon_id, label, sort_order)
                VALUES (:pkg, :prop, :addon, :label, :order)
                """
            ),
            {
                "pkg": package_id, "prop": property_id, "addon": inc.addon_id,
                "label": inc.label.strip(), "order": order,
            },
        )


def _promo_state(row, today: date) -> tuple[str, str]:
    """What the badge should say, combining status, dates and usage."""
    if row["status"] != "active":
        return "inactive", "Inactive"
    if row["valid_to"] < today:
        return "expired", "Expired"
    if row["valid_from"] > today:
        return "scheduled", "Scheduled"
    if row["usage_limit"] is not None and row["usage_count"] >= row["usage_limit"]:
        return "exhausted", "Limit reached"
    return "active", "Active"


def _promo_out(row, today: date) -> PromoOut:
    state, label = _promo_state(row, today)
    value_label = (
        f"{int(row['discount_value'])}%" if row["discount_type"] == "percent"
        else f"₹{row['discount_value']:,.0f}"
    )
    return PromoOut(**dict(row), state=state, state_label=label, value_label=value_label)


# --------------------------------------------------------------------------
# Add-ons
# --------------------------------------------------------------------------
@package_router.get("/addons", response_model=list[AddonOut])
def list_addons(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT a.id, a.code, a.name, a.description, a.category, a.price,
                   a.pricing_unit, a.status, a.version,
                   (SELECT count(DISTINCT pi.package_id)
                      FROM property.package_inclusions pi
                     WHERE pi.addon_id = a.id) AS used_in_packages
            FROM property.addons a
            WHERE a.property_id = :prop
            ORDER BY a.category, a.name
            """
        ),
        {"prop": property_id},
    ).mappings().all()
    return [
        AddonOut(
            **r, category_label=CATEGORY_LABELS.get(r["category"], r["category"]),
            pricing_unit_label=UNIT_LABELS.get(r["pricing_unit"], r["pricing_unit"]),
        )
        for r in rows
    ]


@package_router.post(
    "/addons", response_model=AddonOut, status_code=status.HTTP_201_CREATED
)
def create_addon(
    body: AddonIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate(body.category, ADDON_CATEGORIES, "category")
    _validate(body.pricing_unit, PRICING_UNITS, "pricing_unit")
    _validate(body.status, STATUSES, "status")
    if body.price < 0:
        raise HTTPException(status_code=422, detail="Price cannot be negative")
    addon_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.addons
                    (id, organization_id, property_id, code, name, description,
                     category, price, pricing_unit, status, created_by, updated_by)
                VALUES (:id, :org, :prop, :code, :name, :description, :category,
                        :price, :unit, :status, :actor, :actor)
                """
            ),
            {
                "id": addon_id, "org": caller.organization_id, "prop": property_id,
                "code": body.code.strip().upper(), "name": body.name.strip(),
                "description": body.description, "category": body.category,
                "price": body.price, "unit": body.pricing_unit,
                "status": body.status, "actor": caller.user_id,
            },
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Add-on code {body.code!r} already exists"
        ) from exc
    record_audit(
        db, action="addon.create", entity_type="addon", entity_id=str(addon_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after={"code": body.code, "name": body.name},
    )
    return [a for a in list_addons(property_id, caller, db) if a.id == addon_id][0]


@package_router.put("/addons/{addon_id}", response_model=AddonOut)
def update_addon(
    addon_id: uuid.UUID,
    property_id: uuid.UUID,
    body: AddonUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate(body.category, ADDON_CATEGORIES, "category")
    _validate(body.pricing_unit, PRICING_UNITS, "pricing_unit")
    _validate(body.status, STATUSES, "status")
    if body.price < 0:
        raise HTTPException(status_code=422, detail="Price cannot be negative")
    try:
        result = db.execute(
            text(
                """
                UPDATE property.addons
                   SET code = :code, name = :name, description = :description,
                       category = :category, price = :price, pricing_unit = :unit,
                       status = :status, updated_at = now(), updated_by = :actor,
                       version = version + 1
                 WHERE id = :id AND property_id = :prop AND version = :version
                """
            ),
            {
                "id": addon_id, "prop": property_id, "code": body.code.strip().upper(),
                "name": body.name.strip(), "description": body.description,
                "category": body.category, "price": body.price,
                "unit": body.pricing_unit, "status": body.status,
                "actor": caller.user_id, "version": body.version,
            },
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Add-on code {body.code!r} already exists"
        ) from exc
    if result.rowcount == 0:
        raise _conflict("Add-on")
    record_audit(
        db, action="addon.update", entity_type="addon", entity_id=str(addon_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after={"code": body.code, "status": body.status},
    )
    return [a for a in list_addons(property_id, caller, db) if a.id == addon_id][0]


# --------------------------------------------------------------------------
# Packages
# --------------------------------------------------------------------------
@package_router.get("/packages", response_model=list[PackageOut])
def list_packages(
    property_id: uuid.UUID,
    search: str | None = None,
    pkg_status: str | None = Query(default=None, alias="status"),
    featured_only: bool = False,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate(pkg_status, STATUSES, "status")
    where = ["p.property_id = :prop"]
    params: dict[str, Any] = {"prop": property_id}
    if search:
        where.append("(p.name ILIKE :q OR p.code ILIKE :q OR p.tagline ILIKE :q)")
        params["q"] = f"%{search}%"
    if pkg_status:
        where.append("p.status = :status")
        params["status"] = pkg_status
    if featured_only:
        where.append("p.is_featured")
    rows = db.execute(
        text(f"{_PACKAGE_SELECT} WHERE {' AND '.join(where)} "
             "ORDER BY p.status, p.valid_from, p.name"),
        params,
    ).mappings().all()
    inc = _inclusions(db, [r["id"] for r in rows])
    return [_package_out(r, inc.get(r["id"], [])) for r in rows]


@package_router.get("/packages/{package_id}", response_model=PackageOut)
def get_package(
    package_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    return _one_package(db, package_id, property_id)


def _package_params(body: PackageIn, pid: uuid.UUID, prop: uuid.UUID, caller: Caller):
    return {
        "id": pid, "org": caller.organization_id, "prop": prop,
        "code": body.code.strip().upper(), "name": body.name.strip(),
        "tagline": body.tagline, "description": body.description,
        "badge": (body.badge or "").strip() or None,
        "valid_from": body.valid_from, "valid_to": body.valid_to,
        "direction": body.adjustment_direction, "adj_type": body.adjustment_type,
        "adj_value": body.adjustment_value, "flat_rate": body.flat_rate,
        "min_nights": body.min_nights,
        "max_nights": body.max_nights, "featured": body.is_featured,
        "status": body.status, "actor": caller.user_id,
    }


@package_router.post(
    "/packages", response_model=PackageOut, status_code=status.HTTP_201_CREATED
)
def create_package(
    body: PackageIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_package(body)
    package_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.packages
                    (id, organization_id, property_id, code, name, tagline,
                     description, badge, valid_from, valid_to,
                     adjustment_direction, adjustment_type, adjustment_value,
                     flat_rate, min_nights, max_nights, is_featured, status,
                     created_by, updated_by)
                VALUES (:id, :org, :prop, :code, :name, :tagline, :description,
                        :badge, :valid_from, :valid_to, :direction, :adj_type,
                        :adj_value, :flat_rate, :min_nights, :max_nights,
                        :featured, :status, :actor, :actor)
                """
            ),
            _package_params(body, package_id, property_id, caller),
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Package code {body.code!r} already exists"
        ) from exc
    _replace_links(
        db, package_id=package_id, property_id=property_id,
        room_type_ids=body.room_type_ids, inclusions=body.inclusions,
    )
    record_audit(
        db, action="package.create", entity_type="package", entity_id=str(package_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={"code": body.code, "name": body.name,
               "valid": f"{body.valid_from} to {body.valid_to}"},
        reason=body.reason,
    )
    return _one_package(db, package_id, property_id)


@package_router.put("/packages/{package_id}", response_model=PackageOut)
def update_package(
    package_id: uuid.UUID,
    property_id: uuid.UUID,
    body: PackageUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_package(body)
    params = _package_params(body, package_id, property_id, caller)
    params["version"] = body.version
    try:
        result = db.execute(
            text(
                """
                UPDATE property.packages
                   SET code = :code, name = :name, tagline = :tagline,
                       description = :description, badge = :badge,
                       valid_from = :valid_from, valid_to = :valid_to,
                       adjustment_direction = :direction,
                       adjustment_type = :adj_type, adjustment_value = :adj_value,
                       flat_rate = :flat_rate, min_nights = :min_nights, max_nights = :max_nights,
                       is_featured = :featured, status = :status,
                       updated_at = now(), updated_by = :actor,
                       version = version + 1
                 WHERE id = :id AND property_id = :prop AND version = :version
                """
            ),
            params,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Package code {body.code!r} already exists"
        ) from exc
    if result.rowcount == 0:
        # Either it is gone, or someone else saved first.
        exists = db.execute(
            text("SELECT 1 FROM property.packages WHERE id = :id AND property_id = :prop"),
            {"id": package_id, "prop": property_id},
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="Package not found")
        raise _conflict("Package")
    _replace_links(
        db, package_id=package_id, property_id=property_id,
        room_type_ids=body.room_type_ids, inclusions=body.inclusions,
    )
    record_audit(
        db, action="package.update", entity_type="package", entity_id=str(package_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after={"code": body.code, "name": body.name},
        reason=body.reason,
    )
    return _one_package(db, package_id, property_id)


@package_router.post("/packages/{package_id}/status", response_model=PackageOut)
def set_package_status(
    package_id: uuid.UUID,
    property_id: uuid.UUID,
    body: StatusIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Packages are retired by deactivating them: past stays were sold on them."""
    assert_property_in_org(db, caller, property_id)
    _validate(body.status, STATUSES, "status")
    result = db.execute(
        text(
            """
            UPDATE property.packages
               SET status = :status, updated_at = now(), updated_by = :actor,
                   version = version + 1
             WHERE id = :id AND property_id = :prop AND version = :version
            """
        ),
        {
            "status": body.status, "id": package_id, "prop": property_id,
            "actor": caller.user_id, "version": body.version,
        },
    )
    if result.rowcount == 0:
        exists = db.execute(
            text("SELECT 1 FROM property.packages WHERE id = :id AND property_id = :prop"),
            {"id": package_id, "prop": property_id},
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="Package not found")
        raise _conflict("Package")
    record_audit(
        db,
        action="package.activate" if body.status == "active" else "package.deactivate",
        entity_type="package", entity_id=str(package_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, reason=body.reason,
    )
    return _one_package(db, package_id, property_id)


@package_router.post("/packages/{package_id}/image", response_model=PackageOut)
async def upload_package_image(
    package_id: uuid.UUID,
    property_id: uuid.UUID,
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    existing = db.execute(
        text(
            "SELECT image_storage_key FROM property.packages "
            "WHERE id = :id AND property_id = :prop"
        ),
        {"id": package_id, "prop": property_id},
    ).mappings().first()
    if existing is None:
        raise HTTPException(status_code=404, detail="Package not found")

    content, content_type = await _read_upload(file)
    key = build_key("packages", str(property_id), str(package_id), content_type=content_type)
    try:
        put_object(_STORE, key, content, content_type)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.execute(
        text(
            """
            UPDATE property.packages
               SET image_storage_key = :key, image_url = NULL, updated_at = now(),
                   updated_by = :actor, version = version + 1
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"key": key, "id": package_id, "prop": property_id, "actor": caller.user_id},
    )
    if existing["image_storage_key"]:
        delete_object(_STORE, existing["image_storage_key"])
    record_audit(
        db, action="package.image.set", entity_type="package",
        entity_id=str(package_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"content_type": content_type, "size_bytes": len(content)},
    )
    return _one_package(db, package_id, property_id)


@package_router.delete("/packages/{package_id}/image", response_model=PackageOut)
def delete_package_image(
    package_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            "SELECT image_storage_key FROM property.packages "
            "WHERE id = :id AND property_id = :prop"
        ),
        {"id": package_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Package not found")
    db.execute(
        text(
            """
            UPDATE property.packages
               SET image_storage_key = NULL, image_url = NULL, updated_at = now(),
                   updated_by = :actor, version = version + 1
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": package_id, "prop": property_id, "actor": caller.user_id},
    )
    if row["image_storage_key"]:
        delete_object(_STORE, row["image_storage_key"])
    record_audit(
        db, action="package.image.delete", entity_type="package",
        entity_id=str(package_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
    )
    return _one_package(db, package_id, property_id)


# --------------------------------------------------------------------------
# Promo codes
# --------------------------------------------------------------------------
_PROMO_SELECT = """
    SELECT pc.id, pc.code, pc.description, pc.discount_type, pc.discount_value,
           pc.usage_limit, pc.usage_count, pc.valid_from, pc.valid_to,
           pc.min_nights, pc.package_id, pc.status, pc.version,
           p.name AS package_name
    FROM property.promo_codes pc
    LEFT JOIN property.packages p ON p.id = pc.package_id
"""


@package_router.get("/promo-codes", response_model=list[PromoOut])
def list_promo_codes(
    property_id: uuid.UUID,
    search: str | None = None,
    promo_status: str | None = Query(default=None, alias="status"),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _validate(promo_status, STATUSES, "status")
    where = ["pc.property_id = :prop"]
    params: dict[str, Any] = {"prop": property_id}
    if search:
        where.append("(pc.code ILIKE :q OR pc.description ILIKE :q)")
        params["q"] = f"%{search}%"
    if promo_status:
        where.append("pc.status = :status")
        params["status"] = promo_status
    rows = db.execute(
        text(f"{_PROMO_SELECT} WHERE {' AND '.join(where)} ORDER BY pc.code"), params
    ).mappings().all()
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    return [_promo_out(r, today) for r in rows]


def _check_promo(body: PromoIn) -> None:
    _validate(body.discount_type, DISCOUNT_TYPES, "discount_type")
    _validate(body.status, STATUSES, "status")
    if body.discount_value <= 0:
        raise HTTPException(status_code=422, detail="Discount must be greater than zero")
    if body.discount_type == "percent" and body.discount_value > 100:
        raise HTTPException(
            status_code=422, detail="A percentage discount cannot be more than 100%"
        )
    if body.valid_to < body.valid_from:
        raise HTTPException(status_code=422, detail="Expiry date is before the start date")
    if body.usage_limit is not None and body.usage_limit < 0:
        raise HTTPException(status_code=422, detail="Usage limit cannot be negative")
    if body.min_nights is not None and body.min_nights < 1:
        raise HTTPException(status_code=422, detail="Minimum nights must be at least 1")


@package_router.post(
    "/promo-codes", response_model=PromoOut, status_code=status.HTTP_201_CREATED
)
def create_promo_code(
    body: PromoIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "create")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_promo(body)
    promo_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO property.promo_codes
                    (id, organization_id, property_id, code, description,
                     discount_type, discount_value, usage_limit, valid_from,
                     valid_to, min_nights, package_id, status,
                     created_by, updated_by)
                VALUES (:id, :org, :prop, :code, :description, :dtype, :dvalue,
                        :limit, :valid_from, :valid_to, :min_nights, :package,
                        :status, :actor, :actor)
                """
            ),
            {
                "id": promo_id, "org": caller.organization_id, "prop": property_id,
                "code": body.code.strip().upper(), "description": body.description,
                "dtype": body.discount_type, "dvalue": body.discount_value,
                "limit": body.usage_limit, "valid_from": body.valid_from,
                "valid_to": body.valid_to, "min_nights": body.min_nights,
                "package": body.package_id, "status": body.status,
                "actor": caller.user_id,
            },
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Promo code {body.code!r} already exists"
        ) from exc
    record_audit(
        db, action="promo_code.create", entity_type="promo_code",
        entity_id=str(promo_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"code": body.code, "discount": str(body.discount_value)},
        reason=body.reason,
    )
    return [p for p in list_promo_codes(property_id, None, None, caller, db)
            if p.id == promo_id][0]


@package_router.put("/promo-codes/{promo_id}", response_model=PromoOut)
def update_promo_code(
    promo_id: uuid.UUID,
    property_id: uuid.UUID,
    body: PromoUpdate,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check_promo(body)
    current = db.execute(
        text(
            "SELECT usage_count FROM property.promo_codes "
            "WHERE id = :id AND property_id = :prop"
        ),
        {"id": promo_id, "prop": property_id},
    ).mappings().first()
    if current is None:
        raise HTTPException(status_code=404, detail="Promo code not found")
    # Lowering the limit below what has already been redeemed would make the
    # stored counter contradict the log.
    if body.usage_limit is not None and body.usage_limit < current["usage_count"]:
        raise HTTPException(
            status_code=422,
            detail=f"This code has already been redeemed {current['usage_count']} "
                   f"time(s); the limit cannot be lower than that.",
        )
    try:
        result = db.execute(
            text(
                """
                UPDATE property.promo_codes
                   SET code = :code, description = :description,
                       discount_type = :dtype, discount_value = :dvalue,
                       usage_limit = :limit, valid_from = :valid_from,
                       valid_to = :valid_to, min_nights = :min_nights,
                       package_id = :package, status = :status,
                       updated_at = now(), updated_by = :actor,
                       version = version + 1
                 WHERE id = :id AND property_id = :prop AND version = :version
                """
            ),
            {
                "id": promo_id, "prop": property_id,
                "code": body.code.strip().upper(), "description": body.description,
                "dtype": body.discount_type, "dvalue": body.discount_value,
                "limit": body.usage_limit, "valid_from": body.valid_from,
                "valid_to": body.valid_to, "min_nights": body.min_nights,
                "package": body.package_id, "status": body.status,
                "actor": caller.user_id, "version": body.version,
            },
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Promo code {body.code!r} already exists"
        ) from exc
    if result.rowcount == 0:
        raise _conflict("Promo code")
    record_audit(
        db, action="promo_code.update", entity_type="promo_code",
        entity_id=str(promo_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"code": body.code, "status": body.status}, reason=body.reason,
    )
    return [p for p in list_promo_codes(property_id, None, None, caller, db)
            if p.id == promo_id][0]


@package_router.post("/promo-codes/{promo_id}/redeem", response_model=PromoOut)
def redeem_promo_code(
    promo_id: uuid.UUID,
    property_id: uuid.UUID,
    body: RedeemIn,
    caller: Caller = Depends(require_permission("rooms", "edit")),
    db: Session = Depends(get_session),
):
    """Record one redemption.

    The row is locked and the limit re-checked inside the transaction, so two
    concurrent redemptions cannot both take the last remaining use.
    """
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT id, code, status, valid_from, valid_to, usage_limit, usage_count
            FROM property.promo_codes
            WHERE id = :id AND property_id = :prop
            FOR UPDATE
            """
        ),
        {"id": promo_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Promo code not found")

    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    state, label = _promo_state(row, today)
    if state != "active":
        raise HTTPException(
            status_code=409, detail=f"Promo code {row['code']} cannot be redeemed: {label}"
        )

    db.execute(
        text(
            "UPDATE property.promo_codes SET usage_count = usage_count + 1, "
            "updated_at = now(), version = version + 1 WHERE id = :id"
        ),
        {"id": promo_id},
    )
    db.execute(
        text(
            """
            INSERT INTO property.promo_redemptions
                (organization_id, property_id, promo_code_id, reservation_id,
                 guest_name, discount_amount, redeemed_by)
            VALUES (:org, :prop, :code, :res, :guest, :amount, :actor)
            """
        ),
        {
            "org": caller.organization_id, "prop": property_id, "code": promo_id,
            "res": body.reservation_id, "guest": body.guest_name,
            "amount": body.discount_amount, "actor": caller.user_id,
        },
    )
    record_audit(
        db, action="promo_code.redeem", entity_type="promo_code",
        entity_id=str(promo_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"guest": body.guest_name,
               "discount_amount": _jsonable(body.discount_amount)},
    )
    return [p for p in list_promo_codes(property_id, None, None, caller, db)
            if p.id == promo_id][0]


@package_router.get("/promo-redemptions", response_model=list[RedemptionOut])
def list_redemptions(
    property_id: uuid.UUID,
    promo_code_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    where = ["r.property_id = :prop"]
    params: dict[str, Any] = {"prop": property_id, "limit": limit}
    if promo_code_id:
        where.append("r.promo_code_id = :code")
        params["code"] = promo_code_id
    rows = db.execute(
        text(
            f"""
            SELECT r.id, r.promo_code_id, pc.code, r.guest_name, r.reservation_id,
                   res.number AS reservation_number, r.discount_amount,
                   u.display_name AS redeemed_by_name, r.redeemed_at
            FROM property.promo_redemptions r
            JOIN property.promo_codes pc ON pc.id = r.promo_code_id
            LEFT JOIN booking.reservations res ON res.id = r.reservation_id
            LEFT JOIN iam.users u ON u.id = r.redeemed_by
            WHERE {' AND '.join(where)}
            ORDER BY r.redeemed_at DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    return [RedemptionOut(**r) for r in rows]


@package_router.get("/promotions/stats", response_model=PromotionStats)
def promotion_stats(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM property.packages WHERE property_id = :prop) AS packages,
              (SELECT count(*) FROM property.packages
                WHERE property_id = :prop AND status = 'active') AS active_packages,
              (SELECT count(*) FROM property.promo_codes WHERE property_id = :prop)
                AS promo_codes,
              (SELECT count(*) FROM property.promo_codes
                WHERE property_id = :prop AND status = 'active'
                  AND CURRENT_DATE BETWEEN valid_from AND valid_to) AS active_promo_codes,
              (SELECT count(*) FROM property.promo_redemptions WHERE property_id = :prop)
                AS redemptions
            """
        ),
        {"prop": property_id},
    ).mappings().first()
    return PromotionStats(**row)
