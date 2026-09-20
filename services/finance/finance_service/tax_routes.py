"""Tax and Service Charge Setup API (screen 118).

A tax rule is versioned by effective date. The row the screen lists is the
**current revision** — the newest one whose ``effective_from`` has arrived — and
"Schedule Change" adds a later revision beside it rather than editing history.
That matters because a folio posted last month must still be able to say which
rate it was charged under.

So there is no destructive edit of a past rate here. Editing changes the current
revision; scheduling adds the next one; deactivating stops it applying. Nothing
rewrites what a guest was already billed.

Access follows ``property`` view/update: these are per-property configuration,
set by whoever configures the property, not a system-wide setting.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_session
from .tax_engine import compute_tax, resolve_rules
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

tax_router = APIRouter(tags=["tax-charges"], route_class=TransactionalRoute)

CHARGE_TYPES = ("tax_group", "gst_component", "service_charge", "other_tax")
RATE_TYPES = ("percent", "amount")
AMOUNT_BASES = ("per_night", "per_person", "per_stay", "per_unit")
APPLY_AS = ("exclusive", "inclusive")
STATUSES = ("active", "inactive")
APPLICABILITY = (
    "rooms", "spa", "fnb_outlets", "banquets_events", "in_room_dining",
    "other_services",
)

CHARGE_TYPE_LABELS = {
    "tax_group": "Tax Group", "gst_component": "GST Component",
    "service_charge": "Service Charge", "other_tax": "Other Tax",
}
APPLICABILITY_LABELS = {
    "rooms": "Rooms", "spa": "Spa", "fnb_outlets": "F&B Outlets",
    "banquets_events": "Banquets & Events", "in_room_dining": "In-room Dining",
    "other_services": "Other Services",
}
BASIS_LABELS = {
    "per_night": "per night", "per_person": "per person",
    "per_stay": "per stay", "per_unit": "per unit",
}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class TaxComponent(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    rate: Decimal


class TaxChargeIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=120)
    charge_type: str = "other_tax"
    rate_type: str = "percent"
    rate_value: Decimal = Decimal("0")
    amount_basis: str | None = None
    apply_as: str = "exclusive"
    applicability: list[str] = Field(default_factory=list)
    income_account: str | None = Field(default=None, max_length=80)
    parent_group_id: uuid.UUID | None = None
    remarks: str | None = Field(default=None, max_length=400)
    effective_from: date
    effective_to: date | None = None
    status: str = "active"
    #: For a tax group: the rate this area pays unless a charge names another.
    is_default: bool = False
    components: list[TaxComponent] = Field(default_factory=list)
    reason: str | None = None


class TaxChargeUpdate(TaxChargeIn):
    version: int


class ScheduleIn(TaxChargeIn):
    """A future revision. ``effective_from`` must be later than the current one."""


class StatusIn(BaseModel):
    status: str
    version: int
    reason: str | None = None


class TaxChargeOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    revision: int
    charge_type: str
    charge_type_label: str
    rate_type: str
    rate_value: Decimal
    amount_basis: str | None = None
    rate_label: str
    apply_as: str
    applicability: list[str] = Field(default_factory=list)
    applicability_label: str
    income_account: str | None = None
    parent_group_id: uuid.UUID | None = None
    parent_group_code: str | None = None
    remarks: str | None = None
    effective_from: date
    effective_to: date | None = None
    status: str
    is_default: bool = False
    components: list[TaxComponent] = Field(default_factory=list)
    scheduled_count: int = 0
    scheduled_from: date | None = None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by_name: str | None = None


class TaxStatsOut(BaseModel):
    tax_groups: int
    tax_groups_inactive: int
    gst_components: int
    gst_component_codes: list[str] = Field(default_factory=list)
    service_charges: int
    scheduled_changes: int
    other_taxes: int
    other_tax_names: list[str] = Field(default_factory=list)
    total: int


class TaxChargeListOut(BaseModel):
    items: list[TaxChargeOut]
    total: int
    stats: TaxStatsOut


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


def _check(body: TaxChargeIn) -> None:
    _validate(body.charge_type, CHARGE_TYPES, "charge_type")
    _validate(body.rate_type, RATE_TYPES, "rate_type")
    _validate(body.apply_as, APPLY_AS, "apply_as")
    _validate(body.status, STATUSES, "status")
    for item in body.applicability:
        _validate(item, APPLICABILITY, "applicability")
    if body.rate_value < 0:
        raise HTTPException(status_code=422, detail="Rate cannot be negative")
    if body.rate_type == "percent":
        if body.rate_value > 100:
            raise HTTPException(
                status_code=422, detail="A percentage rate cannot be more than 100%"
            )
        if body.amount_basis is not None:
            raise HTTPException(
                status_code=422,
                detail="A percentage rate has no charging basis; leave it blank",
            )
    else:
        _validate(body.amount_basis, AMOUNT_BASES, "amount_basis")
        if body.amount_basis is None:
            raise HTTPException(
                status_code=422,
                detail="A flat amount must say what it is charged per "
                       f"({', '.join(AMOUNT_BASES)})",
            )
    if body.effective_to is not None and body.effective_to < body.effective_from:
        raise HTTPException(
            status_code=422, detail="Effective-to date is before effective-from"
        )
    if not body.applicability:
        raise HTTPException(
            status_code=422, detail="Choose at least one thing this applies to"
        )
    if body.is_default and body.charge_type != "tax_group":
        raise HTTPException(
            status_code=422,
            detail="Only a tax group can be the default rate for an area",
        )
    # A group that splits into components must split into the rate it charges.
    if body.charge_type == "tax_group" and body.components:
        total = sum(c.rate for c in body.components)
        if total != body.rate_value:
            raise HTTPException(
                status_code=422,
                detail=f"Components add up to {total}%, but the group's rate is "
                       f"{body.rate_value}%. They must match.",
            )


def _num(value) -> str:
    """Trim trailing zeros without falling into scientific notation.

    Decimal("10").normalize() is Decimal("1E+1"), which would print a 10%
    service charge as "1E+1%". Fixed-point formatting avoids that.
    """
    return format(Decimal(value).normalize(), "f")


def _rate_label(row) -> str:
    if row["rate_type"] == "amount":
        basis = BASIS_LABELS.get(row["amount_basis"], row["amount_basis"] or "")
        return f"₹{Decimal(row['rate_value']):,.0f} ({basis})"
    rate = _num(row["rate_value"])
    parts = (row["calculation_rule"] or {}).get("components") or []
    if parts:
        inner = " ".join(f"{p['code']} {_num(p['rate'])}%" for p in parts)
        return f"{rate}% ({inner})"
    if row["charge_type"] == "gst_component" and Decimal(row["rate_value"]) == 0:
        return "As per rate"
    return f"{rate}%"


def _out(row) -> TaxChargeOut:
    rule = row["calculation_rule"] or {}
    applic = list(row["applicability"] or [])
    return TaxChargeOut(
        id=row["id"], code=row["code"], name=row["name"], revision=row["revision"],
        charge_type=row["charge_type"],
        charge_type_label=CHARGE_TYPE_LABELS.get(row["charge_type"], row["charge_type"]),
        rate_type=row["rate_type"], rate_value=row["rate_value"],
        amount_basis=row["amount_basis"], rate_label=_rate_label(row),
        apply_as=row["apply_as"], applicability=applic,
        applicability_label=", ".join(
            APPLICABILITY_LABELS.get(a, a) for a in applic
        ) or "—",
        income_account=row["income_account"], parent_group_id=row["parent_group_id"],
        parent_group_code=row.get("parent_group_code"), remarks=row["remarks"],
        effective_from=row["effective_from"], effective_to=row["effective_to"],
        status=row["status"], is_default=bool(row["is_default"]),
        components=[TaxComponent(**c) for c in (rule.get("components") or [])],
        scheduled_count=row.get("scheduled_count") or 0,
        scheduled_from=row.get("scheduled_from"),
        version=row["version"], created_at=row["created_at"],
        updated_at=row["updated_at"], updated_by_name=row.get("updated_by_name"),
    )


# The current revision of each code: the newest one already in effect. A rule
# whose first revision starts in the future has none in effect yet, so the
# earliest is shown instead — otherwise it would vanish from the screen.
_CURRENT_SQL = """
    SELECT DISTINCT ON (t.code) t.*,
           pg.code AS parent_group_code,
           u.display_name AS updated_by_name,
           (SELECT count(*) FROM finance.tax_rules s
             WHERE s.property_id = t.property_id AND s.code = t.code
               AND s.effective_from > CURRENT_DATE) AS scheduled_count,
           (SELECT min(s.effective_from) FROM finance.tax_rules s
             WHERE s.property_id = t.property_id AND s.code = t.code
               AND s.effective_from > CURRENT_DATE) AS scheduled_from
    FROM finance.tax_rules t
    LEFT JOIN finance.tax_rules pg ON pg.id = t.parent_group_id
    LEFT JOIN iam.users u ON u.id = t.updated_by
    WHERE t.property_id = :prop
    ORDER BY t.code,
             (t.effective_from <= CURRENT_DATE) DESC,
             -- Closest to today: the newest that has started, or, when none
             -- has, the one that starts soonest.
             abs(t.effective_from - CURRENT_DATE),
             t.revision DESC
"""


def _one(db: Session, rule_id: uuid.UUID, property_id: uuid.UUID) -> TaxChargeOut:
    """The revision with this id — not "the current one for its code".

    A write must answer with the row it actually touched, or deactivating a
    superseded revision looks like it did nothing.
    """
    row = db.execute(
        text(
            """
            SELECT t.*, pg.code AS parent_group_code,
                   u.display_name AS updated_by_name,
                   (SELECT count(*) FROM finance.tax_rules s
                     WHERE s.property_id = t.property_id AND s.code = t.code
                       AND s.effective_from > CURRENT_DATE) AS scheduled_count,
                   (SELECT min(s.effective_from) FROM finance.tax_rules s
                     WHERE s.property_id = t.property_id AND s.code = t.code
                       AND s.effective_from > CURRENT_DATE) AS scheduled_from
            FROM finance.tax_rules t
            LEFT JOIN finance.tax_rules pg ON pg.id = t.parent_group_id
            LEFT JOIN iam.users u ON u.id = t.updated_by
            WHERE t.id = :id AND t.property_id = :prop
            """
        ),
        {"prop": property_id, "id": rule_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Tax or charge not found")
    return _out(row)


def _snapshot(db: Session, rule_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        text(
            "SELECT code, name, charge_type, rate_type, rate_value, amount_basis, "
            "apply_as, applicability, income_account, effective_from, effective_to, "
            "status, revision FROM finance.tax_rules WHERE id = :id"
        ),
        {"id": rule_id},
    ).mappings().first()
    if row is None:
        return None
    return {
        k: (str(v) if isinstance(v, (Decimal, uuid.UUID, date)) else v)
        for k, v in dict(row).items()
    }


def _params(body: TaxChargeIn, caller: Caller) -> dict[str, Any]:
    import json

    return {
        "code": body.code.strip().upper(), "name": body.name.strip(),
        "charge_type": body.charge_type, "rate_type": body.rate_type,
        "rate_value": body.rate_value, "amount_basis": body.amount_basis,
        "apply_as": body.apply_as, "applicability": body.applicability,
        "income_account": body.income_account, "parent": body.parent_group_id,
        "remarks": body.remarks, "effective_from": body.effective_from,
        "effective_to": body.effective_to, "status": body.status,
        "is_default": body.is_default,
        # float, not model_dump: a Decimal serialises to a JSON *string*, which
        # would store {"rate": "4"} beside the seeded {"rate": 2.5}.
        "rule": json.dumps(
            {"components": [
                {"code": c.code, "rate": float(c.rate)} for c in body.components
            ]}
            if body.components else {}
        ),
        "actor": caller.user_id,
    }


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------
@tax_router.get("/tax-charges", response_model=TaxChargeListOut)
def list_tax_charges(
    property_id: uuid.UUID,
    search: str | None = None,
    charge_type: str | None = None,
    charge_status: str | None = Query(default=None, alias="status"),
    applicability: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    caller: Caller = Depends(require_permission("property", "view")),
    db: Session = Depends(get_session),
):
    """The table, one row per code showing the revision currently in effect."""
    assert_property_in_org(db, caller, property_id)
    _validate(charge_type, CHARGE_TYPES, "charge_type")
    _validate(charge_status, STATUSES, "status")
    _validate(applicability, APPLICABILITY, "applicability")

    where = ["1 = 1"]
    params: dict[str, Any] = {"prop": property_id, "limit": limit, "offset": offset}
    if search:
        where.append("(cur.name ILIKE :q OR cur.code ILIKE :q)")
        params["q"] = f"%{search}%"
    if charge_type:
        where.append("cur.charge_type = :ctype")
        params["ctype"] = charge_type
    if charge_status:
        where.append("cur.status = :status")
        params["status"] = charge_status
    if applicability:
        where.append(":applic = ANY(cur.applicability)")
        params["applic"] = applicability

    clause = " AND ".join(where)
    rows = db.execute(
        text(
            f"""
            SELECT * FROM ({_CURRENT_SQL}) cur
            WHERE {clause}
            ORDER BY cur.charge_type, cur.code
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()
    total = db.execute(
        text(f"SELECT count(*) FROM ({_CURRENT_SQL}) cur WHERE {clause}"), params
    ).scalar_one()

    stats_row = db.execute(
        text(
            f"""
            SELECT
              count(*) FILTER (WHERE charge_type = 'tax_group') AS tax_groups,
              count(*) FILTER (WHERE charge_type = 'tax_group'
                                 AND status = 'inactive') AS tax_groups_inactive,
              count(*) FILTER (WHERE charge_type = 'gst_component') AS gst_components,
              count(*) FILTER (WHERE charge_type = 'service_charge') AS service_charges,
              count(*) FILTER (WHERE charge_type = 'other_tax') AS other_taxes,
              coalesce(sum(scheduled_count), 0) AS scheduled_changes,
              count(*) AS total
            FROM ({_CURRENT_SQL}) cur
            """
        ),
        {"prop": property_id},
    ).mappings().first()
    labels = db.execute(
        text(
            f"""
            SELECT
              array_agg(code ORDER BY code)
                FILTER (WHERE charge_type = 'gst_component') AS gst_codes,
              array_agg(name ORDER BY name)
                FILTER (WHERE charge_type = 'other_tax') AS other_names
            FROM ({_CURRENT_SQL}) cur
            """
        ),
        {"prop": property_id},
    ).mappings().first()

    return TaxChargeListOut(
        items=[_out(r) for r in rows],
        total=total,
        stats=TaxStatsOut(
            **stats_row,
            gst_component_codes=list(labels["gst_codes"] or []),
            other_tax_names=list(labels["other_names"] or []),
        ),
    )


@tax_router.get("/tax-charges/{code}/revisions", response_model=list[TaxChargeOut])
def list_revisions(
    code: str,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "view")),
    db: Session = Depends(get_session),
):
    """Every revision of one rule, newest first — the rate history."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT t.*, pg.code AS parent_group_code,
                   u.display_name AS updated_by_name,
                   0 AS scheduled_count, NULL::date AS scheduled_from
            FROM finance.tax_rules t
            LEFT JOIN finance.tax_rules pg ON pg.id = t.parent_group_id
            LEFT JOIN iam.users u ON u.id = t.updated_by
            WHERE t.property_id = :prop AND t.code = :code
            ORDER BY t.effective_from DESC, t.revision DESC
            """
        ),
        {"prop": property_id, "code": code.upper()},
    ).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Tax or charge not found")
    return [_out(r) for r in rows]


# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------
@tax_router.post(
    "/tax-charges", response_model=TaxChargeOut, status_code=status.HTTP_201_CREATED
)
def create_tax_charge(
    body: TaxChargeIn,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    _check(body)
    rule_id = uuid.uuid4()
    params = _params(body, caller)
    params.update({"id": rule_id, "org": caller.organization_id, "prop": property_id})
    try:
        db.execute(
            text(
                """
                INSERT INTO finance.tax_rules
                    (id, organization_id, property_id, code, revision, name,
                     charge_type, rate_type, rate_value, amount_basis, apply_as,
                     applicability, income_account, parent_group_id, remarks,
                     effective_from, effective_to, status, is_default,
                     calculation_rule, created_by, updated_by)
                VALUES (:id, :org, :prop, :code, 1, :name, :charge_type, :rate_type,
                        :rate_value, :amount_basis, :apply_as,
                        CAST(:applicability AS text[]), :income_account, :parent,
                        :remarks, :effective_from, :effective_to, :status,
                        :is_default, CAST(:rule AS jsonb), :actor, :actor)
                """
            ),
            params,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Code {body.code!r} already exists"
        ) from exc
    record_audit(
        db, action="tax_charge.create", entity_type="tax_rule", entity_id=str(rule_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, after=_snapshot(db, rule_id), reason=body.reason,
    )
    return _one(db, rule_id, property_id)


@tax_router.put("/tax-charges/{rule_id}", response_model=TaxChargeOut)
def update_tax_charge(
    rule_id: uuid.UUID,
    property_id: uuid.UUID,
    body: TaxChargeUpdate,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Correct the revision in effect. To change a rate from a future date, use
    Schedule Change instead — that keeps what was already billed intact."""
    assert_property_in_org(db, caller, property_id)
    _check(body)
    before = _snapshot(db, rule_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Tax or charge not found")
    params = _params(body, caller)
    params.update({"id": rule_id, "prop": property_id, "version": body.version})
    try:
        result = db.execute(
            text(
                """
                UPDATE finance.tax_rules
                   SET code = :code, name = :name, charge_type = :charge_type,
                       rate_type = :rate_type, rate_value = :rate_value,
                       amount_basis = :amount_basis, apply_as = :apply_as,
                       applicability = CAST(:applicability AS text[]),
                       income_account = :income_account, parent_group_id = :parent,
                       remarks = :remarks, effective_from = :effective_from,
                       effective_to = :effective_to, status = :status,
                       is_default = :is_default,
                       calculation_rule = CAST(:rule AS jsonb),
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
            status_code=409, detail=f"Code {body.code!r} already exists"
        ) from exc
    if result.rowcount == 0:
        raise _conflict("Tax or charge")
    record_audit(
        db, action="tax_charge.update", entity_type="tax_rule", entity_id=str(rule_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before=before, after=_snapshot(db, rule_id),
        reason=body.reason,
    )
    return _one(db, rule_id, property_id)


@tax_router.post(
    "/tax-charges/{rule_id}/schedule", response_model=TaxChargeOut,
    status_code=status.HTTP_201_CREATED,
)
def schedule_change(
    rule_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ScheduleIn,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Add the next revision of a rule, effective from a future date."""
    assert_property_in_org(db, caller, property_id)
    _check(body)
    current = db.execute(
        text(
            "SELECT code, revision, effective_from FROM finance.tax_rules "
            "WHERE id = :id AND property_id = :prop"
        ),
        {"id": rule_id, "prop": property_id},
    ).mappings().first()
    if current is None:
        raise HTTPException(status_code=404, detail="Tax or charge not found")
    if body.code.strip().upper() != current["code"]:
        raise HTTPException(
            status_code=422,
            detail="A scheduled change keeps the same code as the rule it revises",
        )
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    if body.effective_from <= today:
        raise HTTPException(
            status_code=422,
            detail="A scheduled change must start in the future. To correct the "
                   "current rate, edit it instead.",
        )
    if body.effective_from <= current["effective_from"]:
        raise HTTPException(
            status_code=422,
            detail=f"The new rate must start after the one it replaces "
                   f"({current['effective_from']}).",
        )
    clash = db.execute(
        text(
            "SELECT 1 FROM finance.tax_rules WHERE property_id = :prop "
            "AND code = :code AND effective_from = :eff"
        ),
        {"prop": property_id, "code": current["code"], "eff": body.effective_from},
    ).first()
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A revision already starts on {body.effective_from}",
        )

    next_revision = db.execute(
        text(
            "SELECT coalesce(max(revision), 0) + 1 FROM finance.tax_rules "
            "WHERE property_id = :prop AND code = :code"
        ),
        {"prop": property_id, "code": current["code"]},
    ).scalar_one()

    new_id = uuid.uuid4()
    params = _params(body, caller)
    params.update({
        "id": new_id, "org": caller.organization_id, "prop": property_id,
        "revision": next_revision,
    })
    db.execute(
        text(
            """
            INSERT INTO finance.tax_rules
                (id, organization_id, property_id, code, revision, name,
                 charge_type, rate_type, rate_value, amount_basis, apply_as,
                 applicability, income_account, parent_group_id, remarks,
                 effective_from, effective_to, status, is_default,
                 calculation_rule, created_by, updated_by)
            VALUES (:id, :org, :prop, :code, :revision, :name, :charge_type,
                    :rate_type, :rate_value, :amount_basis, :apply_as,
                    CAST(:applicability AS text[]), :income_account, :parent,
                    :remarks, :effective_from, :effective_to, :status,
                    :is_default, CAST(:rule AS jsonb), :actor, :actor)
            """
        ),
        params,
    )
    record_audit(
        db, action="tax_charge.schedule", entity_type="tax_rule", entity_id=str(new_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={
            "code": current["code"], "revision": next_revision,
            "effective_from": str(body.effective_from),
            "rate_value": str(body.rate_value),
        },
        reason=body.reason,
    )
    return _one(db, new_id, property_id)


@tax_router.post(
    "/tax-charges/{rule_id}/clone", response_model=TaxChargeOut,
    status_code=status.HTTP_201_CREATED,
)
def clone_tax_charge(
    rule_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Copy a rule to a new code, inactive so it cannot bill anyone by accident."""
    assert_property_in_org(db, caller, property_id)
    source = db.execute(
        text("SELECT * FROM finance.tax_rules WHERE id = :id AND property_id = :prop"),
        {"id": rule_id, "prop": property_id},
    ).mappings().first()
    if source is None:
        raise HTTPException(status_code=404, detail="Tax or charge not found")

    taken = set(
        db.execute(
            text("SELECT code FROM finance.tax_rules WHERE property_id = :prop"),
            {"prop": property_id},
        ).scalars().all()
    )
    suffix = 2
    while f"{source['code'][:46]}_{suffix}" in taken:
        suffix += 1
    new_code = f"{source['code'][:46]}_{suffix}"

    new_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.tax_rules
                (id, organization_id, property_id, code, revision, name,
                 charge_type, rate_type, rate_value, amount_basis, apply_as,
                 applicability, income_account, parent_group_id, remarks,
                 effective_from, effective_to, status, is_default,
                 calculation_rule, created_by, updated_by)
            SELECT :new_id, organization_id, property_id, :code, 1,
                   :name, charge_type, rate_type, rate_value, amount_basis,
                   apply_as, applicability, income_account, parent_group_id,
                   remarks, effective_from, effective_to, 'inactive',
                   is_default, calculation_rule, :actor, :actor
            FROM finance.tax_rules WHERE id = :id
            """
        ),
        {
            "new_id": new_id, "code": new_code,
            "name": f"{source['name']} (Copy)"[:120],
            "id": rule_id, "actor": caller.user_id,
        },
    )
    record_audit(
        db, action="tax_charge.clone", entity_type="tax_rule", entity_id=str(new_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={"copied_from": source["code"], "code": new_code},
    )
    return _one(db, new_id, property_id)


@tax_router.post("/tax-charges/{rule_id}/status", response_model=TaxChargeOut)
def set_tax_charge_status(
    rule_id: uuid.UUID,
    property_id: uuid.UUID,
    body: StatusIn,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Rules are deactivated, never deleted: folios were posted under them."""
    assert_property_in_org(db, caller, property_id)
    _validate(body.status, STATUSES, "status")
    before = _snapshot(db, rule_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Tax or charge not found")
    if body.status == "inactive":
        used_by = db.execute(
            text(
                """
                SELECT count(*) FROM finance.tax_rules child
                 WHERE child.parent_group_id = :id AND child.status = 'active'
                """
            ),
            {"id": rule_id},
        ).scalar_one()
        if used_by:
            raise HTTPException(
                status_code=409,
                detail=f"{used_by} active charge(s) belong to this group. "
                       f"Move or deactivate them first.",
            )
    result = db.execute(
        text(
            """
            UPDATE finance.tax_rules
               SET status = :status, updated_at = now(), updated_by = :actor,
                   version = version + 1
             WHERE id = :id AND property_id = :prop AND version = :version
            """
        ),
        {
            "status": body.status, "id": rule_id, "prop": property_id,
            "actor": caller.user_id, "version": body.version,
        },
    )
    if result.rowcount == 0:
        raise _conflict("Tax or charge")
    record_audit(
        db,
        action="tax_charge.activate" if body.status == "active"
        else "tax_charge.deactivate",
        entity_type="tax_rule", entity_id=str(rule_id),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject, before=before, after=_snapshot(db, rule_id),
        reason=body.reason,
    )
    return _one(db, rule_id, property_id)


# --------------------------------------------------------------------------
# Quote (screen 004)
# --------------------------------------------------------------------------
class QuoteLine(BaseModel):
    code: str
    rate: Decimal
    #: 'percent' — rate is a share of the charge — or 'amount', a flat sum per
    #: night. A caller that labels both as percentages misreports the bill.
    rate_type: str = "percent"
    taxable_amount: Decimal
    tax_amount: Decimal


class TaxQuote(BaseModel):
    """What a stay will actually cost, taxed the way the folio will tax it."""

    room_charge: Decimal
    nights: int
    # Added on top of the room charge.
    tax_total: Decimal
    # Already inside the room charge; shown so the guest can see it, never
    # added again.
    inclusive_tax: Decimal
    total: Decimal
    lines: list[QuoteLine]
    # True when no rule applies at all, so the caller can say "no tax
    # configured" instead of implying the stay is tax-free.
    has_rules: bool


@tax_router.get("/tax-quote", response_model=TaxQuote)
def tax_quote(
    property_id: uuid.UUID,
    amount: Decimal = Query(..., ge=0, description="Room charge for the stay"),
    nights: int = Query(1, ge=1),
    on_date: date | None = Query(None),
    category: str = Query("rooms"),
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """Quote the tax on a stay before it is booked.

    The New Reservation screen used a hard-coded 18%, while the folio posts
    tax from ``finance.tax_rules`` — so the figure quoted to a guest could
    differ from the one that landed on their bill, and nobody would find out
    until checkout.

    This runs the same ``resolve_rules`` and ``compute_tax`` the folio runs,
    on the same date, so the quote and the posting cannot drift. It is a
    quote and nothing else: nothing is written.
    """
    assert_property_in_org(db, caller, property_id)
    day = on_date or db.execute(text("SELECT CURRENT_DATE")).scalar_one()

    rules = resolve_rules(
        db, property_id=property_id, category=category, on_date=day
    )
    # Pass the nights as nights. The rule decides whether a flat levy is
    # charged per night, per stay or per person; this endpoint has no
    # occupancy to offer, so a per-person levy quotes for one.
    result = compute_tax(rules, amount=amount, units=nights, nights=nights)

    return TaxQuote(
        room_charge=amount, nights=nights,
        tax_total=result.exclusive_total,
        inclusive_tax=result.inclusive_total,
        total=amount + result.exclusive_total,
        lines=[
            QuoteLine(code=line.tax_code, rate=line.rate_snapshot,
                      rate_type=line.rate_type,
                      taxable_amount=line.taxable_amount,
                      tax_amount=line.tax_amount)
            for line in result.lines
        ],
        has_rules=bool(rules),
    )
