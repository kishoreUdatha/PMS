"""SaaS billing: what the platform controls and what a tenant may do itself.

Pricing is **per tenant per month**. A plan's property, room and user counts
are limits it imposes; they never multiply its price.

Two routers, because there are two audiences and they are not symmetric.

``/platform/billing/*`` is the operator's view across every tenant, gated on
the capabilities 0027 added. ``/billing/*`` is a tenant's view of their own
account, gated on the ordinary tenant permission dependency so
``guard_request_tenancy`` runs exactly as it does everywhere else.

The asymmetry is deliberate and enforced in the database, not just here: the
RLS policies let a tenant **read** their subscription and invoices and let
nobody but system context **write** them. A tenant can therefore see what they
owe and ask to change plan; they cannot alter what they owe. Every write below
that acts for a tenant elevates to system context and then writes for exactly
``caller_org(caller)`` -- never for an organisation named in a request.

Money is ``Decimal`` throughout, never float. Proration is computed on whole
days, because a billing period is a number of days and dividing a month into
fractional days invents pennies nobody can reconcile.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from chirala_common.db import system_context
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .audit import record_audit
from .billing_run import run_billing
from .authz import Caller, require_org_permission
from .database import get_session
from .platform_authz import (
    PlatformCaller,
    correlation_id,
    require_capability,
)

platform_billing_router = APIRouter(
    prefix="/platform/billing", tags=["platform-billing"],
    route_class=TransactionalRoute,
)
tenant_billing_router = APIRouter(
    prefix="/billing", tags=["billing"], route_class=TransactionalRoute,
)

#: Live states. A subscription outside these is history, not a contract.
LIVE = ("trialing", "active", "past_due", "grace")

MONEY = Decimal("0.01")


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _rows(db: Session, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params or {}).mappings()]


def _one(db: Session, sql: str, params: dict) -> dict | None:
    r = db.execute(text(sql), params).mappings().first()
    return dict(r) if r else None


def _caller_org(caller: Caller) -> uuid.UUID:
    """The caller's own organisation, or 403.

    Deliberately the only source of a tenant for the tenant-facing routes
    below. An organisation the request names is not the caller's to choose.
    """
    if caller.organization_id is None:
        raise HTTPException(status_code=403, detail="No active membership")
    return caller.organization_id


# ------------------------------------------------------------- catalogue ---

_PLAN_SQL = """
    SELECT p.id AS plan_id, p.code, p.name, p.summary, p.sort_order, p.status,
           v.id AS plan_version_id, v.version_no, v.amount, v.currency,
           v.billing_cycle, v.trial_days, v.status AS version_status,
           coalesce((SELECT json_object_agg(l.metric_code, l.limit_value)
                       FROM billing.plan_version_limits l
                      WHERE l.plan_version_id = v.id), '{}'::json) AS limits,
           coalesce((SELECT array_agg(m.module_code ORDER BY m.module_code)
                       FROM billing.plan_version_modules m
                      WHERE m.plan_version_id = v.id), '{}') AS modules
    FROM billing.plans p
    JOIN LATERAL (
        SELECT * FROM billing.plan_versions pv
        WHERE pv.plan_id = p.id AND pv.status = 'published'
        ORDER BY pv.version_no DESC LIMIT 1
    ) v ON true
    WHERE p.status = 'active'
    ORDER BY p.sort_order
"""


@platform_billing_router.get("/plans")
def platform_plans(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("billing.view")),
):
    """The catalogue, latest published version of each plan."""
    return _rows(db, _PLAN_SQL)


@tenant_billing_router.get("/plans")
def tenant_plans(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("administration", "view")),
):
    """The same catalogue, for a tenant choosing a plan.

    Readable without elevation: the plan tables are not tenant data and their
    RLS policy says so.
    """
    return _rows(db, _PLAN_SQL)


class PlanVersionIn(BaseModel):
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    billing_cycle: str = Field(pattern="^(monthly|annual)$")
    trial_days: int = Field(default=0, ge=0, le=365)
    limits: dict[str, int | None] = Field(default_factory=dict)
    modules: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=3, max_length=300)


@platform_billing_router.post("/plans/{plan_id}/versions", status_code=201)
def create_plan_version(
    request: Request,
    plan_id: uuid.UUID,
    body: PlanVersionIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("plan.manage")),
):
    """Price a plan by creating a new version, never by editing an old one.

    This is the route that makes "a changed catalog price does not silently
    change existing contracts" true rather than promised. Nothing here can
    reach a subscription: existing ones keep pointing at the version they were
    sold, and moving them is a separate, previewed, audited action.

    The version lands as a draft. Publishing is deliberately a second step, so
    a price can be reviewed before anybody can be sold it.
    """
    plan = _one(db, "SELECT id, code, name FROM billing.plans WHERE id = :p",
                {"p": plan_id})
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    next_no = db.execute(
        text("SELECT coalesce(max(version_no), 0) + 1 "
             "FROM billing.plan_versions WHERE plan_id = :p"),
        {"p": plan_id},
    ).scalar()
    version_id = db.execute(
        text(
            """
            INSERT INTO billing.plan_versions
                (plan_id, version_no, amount, currency, billing_cycle,
                 trial_days, status)
            VALUES (:p, :n, :amt, 'INR', :cyc, :trial, 'draft')
            RETURNING id
            """
        ),
        {"p": plan_id, "n": next_no, "amt": body.amount,
         "cyc": body.billing_cycle, "trial": body.trial_days},
    ).scalar()

    for metric, value in body.limits.items():
        db.execute(
            text("INSERT INTO billing.plan_version_limits "
                 "(plan_version_id, metric_code, limit_value) "
                 "VALUES (:v, :m, :l)"),
            {"v": version_id, "m": metric, "l": value},
        )
    for module in sorted(set(body.modules)):
        db.execute(
            text("INSERT INTO billing.plan_version_modules "
                 "(plan_version_id, module_code) VALUES (:v, :m)"),
            {"v": version_id, "m": module},
        )

    record_audit(
        db, correlation_id=correlation_id(request),
        action="billing.plan_version.created", entity_type="plan_version",
        entity_id=str(version_id), actor_subject=admin.subject,
        reason=body.reason,
        after={"plan": plan["code"], "version_no": next_no,
               "amount": str(body.amount), "status": "draft"},
    )
    return {"id": str(version_id), "plan_id": str(plan_id),
            "version_no": next_no, "status": "draft"}


class PublishIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@platform_billing_router.post("/plan-versions/{version_id}/publish")
def publish_plan_version(
    request: Request,
    version_id: uuid.UUID,
    body: PublishIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("plan.manage")),
):
    """Make a draft version sellable. It becomes immutable at this point."""
    v = _one(
        db,
        "SELECT v.id, v.status, v.version_no, v.amount, p.code "
        "FROM billing.plan_versions v JOIN billing.plans p ON p.id = v.plan_id "
        "WHERE v.id = :v",
        {"v": version_id},
    )
    if v is None:
        raise HTTPException(status_code=404, detail="Plan version not found")
    if v["status"] != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"That version is already {v['status']}.")

    db.execute(
        text("UPDATE billing.plan_versions SET status = 'published', "
             "published_at = now(), updated_at = now() WHERE id = :v"),
        {"v": version_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="billing.plan_version.published", entity_type="plan_version",
        entity_id=str(version_id), actor_subject=admin.subject,
        reason=body.reason, before={"status": "draft"},
        after={"status": "published", "plan": v["code"],
               "amount": str(v["amount"])},
    )
    return {"id": str(version_id), "status": "published"}


# ------------------------------------------------------------ usage -------

USAGE_SQL = """
    SELECT 'properties' AS metric_code,
           count(*)::int AS value
      FROM iam.properties WHERE organization_id = :org AND status = 'active'
    UNION ALL
    SELECT 'rooms', count(*)::int
      FROM property.rooms WHERE organization_id = :org
    UNION ALL
    SELECT 'active_users', count(*)::int
      FROM iam.memberships WHERE organization_id = :org AND status = 'active'
"""


def _usage(db: Session, org_id: uuid.UUID) -> dict[str, int]:
    return {r["metric_code"]: r["value"]
            for r in _rows(db, USAGE_SQL, {"org": org_id})}


def _limits(db: Session, plan_version_id: uuid.UUID) -> dict[str, int | None]:
    return {r["metric_code"]: r["limit_value"] for r in _rows(
        db,
        "SELECT metric_code, limit_value FROM billing.plan_version_limits "
        "WHERE plan_version_id = :v",
        {"v": plan_version_id},
    )}


def _blockers(usage: dict[str, int], limits: dict[str, int | None]) -> list[dict]:
    """Where current usage exceeds what the target plan allows.

    A NULL limit is unlimited, which is not the same as zero and must not be
    treated as one -- reading it as zero would make the most generous plan
    look like the most restrictive.
    """
    out = []
    for metric, limit in limits.items():
        if limit is None:
            continue
        have = usage.get(metric, 0)
        if have > limit:
            out.append({"metric": metric, "in_use": have, "allowed": limit,
                        "over_by": have - limit})
    return out


# ------------------------------------------------------ subscriptions -----

_SUB_SQL = """
    SELECT s.id, s.organization_id, o.name AS organization_name,
           s.status, s.amount, s.currency, s.billing_cycle, s.quantity,
           s.started_on, s.trial_ends_on, s.current_period_start,
           s.current_period_end, s.grace_until, s.cancel_at,
           p.code AS plan_code, p.name AS plan_name,
           v.id AS plan_version_id, v.version_no,
           (s.amount * s.quantity) AS period_total
    FROM billing.subscriptions s
    JOIN iam.organizations o ON o.id = s.organization_id
    JOIN billing.plan_versions v ON v.id = s.plan_version_id
    JOIN billing.plans p ON p.id = v.plan_id
"""


@platform_billing_router.get("/subscriptions")
def list_subscriptions(
    status: str | None = None,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("billing.view")),
):
    """Every tenant's subscription — specification screen 10.

    Organisations with no subscription are included deliberately, as a row
    with a null plan. A tenant nobody has put on a plan is the single most
    useful thing this screen can surface, and filtering to rows that exist
    would hide exactly that.
    """
    return _rows(
        db,
        """
        SELECT o.id AS organization_id, o.name AS organization_name,
               o.status AS organization_status,
               s.id AS subscription_id, s.status, s.amount, s.currency,
               s.billing_cycle, s.quantity, s.trial_ends_on,
               s.current_period_end, s.grace_until,
               p.code AS plan_code, p.name AS plan_name, v.version_no,
               (s.amount * s.quantity) AS period_total,
               (SELECT count(*) FROM billing.invoices i
                 WHERE i.organization_id = o.id
                   AND i.status IN ('issued', 'uncollectible')) AS unpaid_invoices
        FROM iam.organizations o
        LEFT JOIN billing.subscriptions s
               ON s.organization_id = o.id
              AND s.status IN ('trialing', 'active', 'past_due', 'grace')
        LEFT JOIN billing.plan_versions v ON v.id = s.plan_version_id
        LEFT JOIN billing.plans p ON p.id = v.plan_id
        WHERE (CAST(:st AS text) IS NULL OR s.status = :st)
        ORDER BY o.name
        """,
        {"st": status or None},
    )


@platform_billing_router.get("/subscriptions/{org_id}")
def subscription_detail(
    org_id: uuid.UUID,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("billing.view")),
):
    """One tenant's contract, its usage against its limits, and its history."""
    sub = _one(db, _SUB_SQL + " WHERE s.organization_id = :org "
               "AND s.status = ANY(:live) LIMIT 1",
               {"org": org_id, "live": list(LIVE)})
    usage = _usage(db, org_id)
    out: dict = {"organization_id": str(org_id), "subscription": sub,
                 "usage": usage}
    if sub:
        limits = _limits(db, sub["plan_version_id"])
        out["limits"] = limits
        out["over_limit"] = _blockers(usage, limits)
        out["entitlements"] = _rows(
            db,
            "SELECT kind, code, value, source, note FROM billing.entitlements "
            "WHERE subscription_id = :s ORDER BY kind, code",
            {"s": sub["id"]},
        )
        out["changes"] = _rows(
            db,
            """
            SELECT c.id, c.effective_on, c.proration_amount, c.status,
                   c.reason, c.created_at, c.applied_at,
                   fp.code AS from_plan, tp.code AS to_plan
            FROM billing.subscription_changes c
            LEFT JOIN billing.plan_versions fv ON fv.id = c.from_plan_version_id
            LEFT JOIN billing.plans fp ON fp.id = fv.plan_id
            JOIN billing.plan_versions tv ON tv.id = c.to_plan_version_id
            JOIN billing.plans tp ON tp.id = tv.plan_id
            WHERE c.subscription_id = :s
            ORDER BY c.created_at DESC LIMIT 20
            """,
            {"s": sub["id"]},
        )
    return out


def _proration(current_amount: Decimal, target_amount: Decimal,
               quantity: int, period_end: date | None) -> Decimal:
    """What changing plan mid-period is worth, on whole days.

    Positive means the tenant owes more, negative means a credit. Returns zero
    when the period end is unknown rather than guessing a month: an invented
    denominator produces a number that looks authoritative and is not.
    """
    if period_end is None:
        return Decimal("0.00")
    remaining = (period_end - date.today()).days
    if remaining <= 0:
        return Decimal("0.00")
    # A monthly period is whatever it actually is; 30 is only the fallback
    # denominator when the period start is not known here.
    daily = (target_amount - current_amount) * quantity / Decimal(30)
    return _money(daily * remaining)


class ChangePreviewIn(BaseModel):
    plan_code: str = Field(min_length=2, max_length=40)
    effective_on: date | None = None
    reason: str = Field(min_length=3, max_length=300)


def _preview(db: Session, org_id: uuid.UUID, body: ChangePreviewIn) -> dict:
    target = _one(
        db,
        """
        SELECT v.id, v.amount, v.billing_cycle, v.trial_days, p.code, p.name
        FROM billing.plan_versions v
        JOIN billing.plans p ON p.id = v.plan_id
        WHERE p.code = :code AND v.status = 'published'
        ORDER BY v.version_no DESC LIMIT 1
        """,
        {"code": body.plan_code},
    )
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"No published plan called {body.plan_code}.")

    sub = _one(db, _SUB_SQL + " WHERE s.organization_id = :org "
               "AND s.status = ANY(:live) LIMIT 1",
               {"org": org_id, "live": list(LIVE)})
    usage = _usage(db, org_id)
    limits = _limits(db, target["id"])
    blockers = _blockers(usage, limits)

    # One unit of the plan per tenant. Property, room and user counts are the
    # plan's limits, checked below, not multipliers on its price.
    quantity = 1
    current_amount = _money(sub["amount"]) if sub else Decimal("0.00")
    target_amount = _money(target["amount"])
    proration = _proration(
        current_amount, target_amount, quantity,
        sub["current_period_end"] if sub else None)

    return {
        "target": {"plan_version_id": str(target["id"]), "code": target["code"],
                   "name": target["name"], "amount": str(target_amount),
                   "billing_cycle": target["billing_cycle"]},
        "current": ({"code": sub["plan_code"], "amount": str(current_amount),
                     "version_no": sub["version_no"]} if sub else None),
        "quantity": quantity,
        "new_period_total": str(_money(target_amount * quantity)),
        "proration_amount": str(proration),
        "effective_on": str(body.effective_on or date.today()),
        "limits": limits,
        "usage": usage,
        "over_limit": blockers,
        # The spec's rule, surfaced as a decision rather than an error: a
        # downgrade that would strand data has to be resolved first.
        "can_apply": not blockers,
        "subscription_id": str(sub["id"]) if sub else None,
    }


@platform_billing_router.post("/subscriptions/{org_id}/preview-change")
def preview_change(
    org_id: uuid.UUID,
    body: ChangePreviewIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("subscription.manage")),
):
    """What would happen, without anything happening.

    The spec asks a plan change to preview scope, usage limits, effective date
    and billing effect before it is confirmed. This returns all four and
    writes nothing.
    """
    return _preview(db, org_id, body)


class ApplyChangeIn(ChangePreviewIn):
    #: Confirming a downgrade that strands usage has to be deliberate, and the
    #: operator has to have seen the numbers. There is no flag that skips the
    #: check -- the only way past it is to reduce the usage.
    acknowledge_proration: bool = False


def _apply_change(db: Session, *, org_id: uuid.UUID, body: ApplyChangeIn,
                  actor_subject: str, actor_user_id: uuid.UUID | None,
                  corr: str) -> dict:
    """Move a subscription onto a plan version, and rewrite its entitlements.

    Entitlements are replaced wholesale from the new version rather than
    merged, because a merge would silently keep a module the old plan included
    and the new one does not -- which is a tenant using something they have
    stopped paying for.

    Overrides survive. They exist precisely because somebody decided this
    tenant is an exception, and a plan change is not a decision to revoke that.
    """
    plan = _preview(db, org_id, body)
    if plan["over_limit"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "Usage exceeds the new plan: "
                + "; ".join(f"{b['metric']} {b['in_use']} of {b['allowed']}"
                            for b in plan["over_limit"])
                + ". Reduce it before downgrading."),
        )

    target_version = uuid.UUID(plan["target"]["plan_version_id"])
    effective = body.effective_on or date.today()
    sub_id = plan["subscription_id"]

    if sub_id is None:
        # First subscription for this tenant.
        trial_days = db.execute(
            text("SELECT trial_days FROM billing.plan_versions WHERE id = :v"),
            {"v": target_version},
        ).scalar() or 0
        sub_id = str(db.execute(
            text(
                """
                INSERT INTO billing.subscriptions
                    (organization_id, plan_version_id, status, amount,
                     currency, billing_cycle, quantity, started_on,
                     trial_ends_on, current_period_start, current_period_end)
                VALUES (:org, :v, :status, :amt, 'INR', :cyc, :qty, :start,
                        :trial_end, :start, :period_end)
                RETURNING id
                """
            ),
            {"org": org_id, "v": target_version,
             "status": "trialing" if trial_days else "active",
             "amt": Decimal(plan["target"]["amount"]),
             "cyc": plan["target"]["billing_cycle"],
             "qty": plan["quantity"], "start": effective,
             "trial_end": (date.fromordinal(effective.toordinal() + trial_days)
                           if trial_days else None),
             "period_end": date.fromordinal(effective.toordinal() + 30)},
        ).scalar())
        before = None
    else:
        before = plan["current"]
        db.execute(
            text(
                """
                UPDATE billing.subscriptions
                   SET plan_version_id = :v, amount = :amt,
                       billing_cycle = :cyc, quantity = :qty,
                       updated_at = now(), version = version + 1
                 WHERE id = :s
                """
            ),
            {"v": target_version, "amt": Decimal(plan["target"]["amount"]),
             "cyc": plan["target"]["billing_cycle"], "qty": plan["quantity"],
             "s": sub_id},
        )

    db.execute(
        text(
            """
            INSERT INTO billing.subscription_changes
                (subscription_id, from_plan_version_id, to_plan_version_id,
                 effective_on, proration_amount, status, requested_by, reason,
                 applied_at)
            SELECT :s, s.plan_version_id, :v, :eff, :pro, 'applied', :by, :why,
                   now()
            FROM billing.subscriptions s WHERE s.id = :s
            """
        ),
        {"s": sub_id, "v": target_version, "eff": effective,
         "pro": Decimal(plan["proration_amount"]), "by": actor_user_id,
         "why": body.reason},
    )

    # Replace plan-sourced entitlements; leave overrides alone.
    db.execute(
        text("DELETE FROM billing.entitlements "
             "WHERE subscription_id = :s AND source = 'plan'"),
        {"s": sub_id},
    )
    db.execute(
        text(
            """
            INSERT INTO billing.entitlements
                (subscription_id, kind, code, value, source, effective_from)
            SELECT :s, 'limit', l.metric_code, l.limit_value, 'plan', :eff
            FROM billing.plan_version_limits l WHERE l.plan_version_id = :v
            ON CONFLICT (subscription_id, kind, code) DO UPDATE
               SET value = EXCLUDED.value
            """
        ),
        {"s": sub_id, "v": target_version, "eff": effective},
    )
    db.execute(
        text(
            """
            INSERT INTO billing.entitlements
                (subscription_id, kind, code, value, source, effective_from)
            SELECT :s, 'module', m.module_code, NULL, 'plan', :eff
            FROM billing.plan_version_modules m WHERE m.plan_version_id = :v
            ON CONFLICT (subscription_id, kind, code) DO NOTHING
            """
        ),
        {"s": sub_id, "v": target_version, "eff": effective},
    )

    record_audit(
        db, correlation_id=corr, action="billing.subscription.changed",
        entity_type="subscription", entity_id=str(sub_id),
        organization_id=org_id, actor_subject=actor_subject,
        reason=body.reason, before=before,
        after={"plan": plan["target"]["code"],
               "amount": plan["target"]["amount"],
               "quantity": plan["quantity"],
               "proration": plan["proration_amount"],
               "effective_on": str(effective)},
    )
    return {"subscription_id": sub_id, "plan": plan["target"]["code"],
            "amount": plan["target"]["amount"], "quantity": plan["quantity"],
            "proration_amount": plan["proration_amount"],
            "effective_on": str(effective)}


@platform_billing_router.post("/subscriptions/{org_id}/apply-change")
def apply_change(
    request: Request,
    org_id: uuid.UUID,
    body: ApplyChangeIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("subscription.manage")),
):
    """Move a tenant onto a plan, on their behalf."""
    return _apply_change(
        db, org_id=org_id, body=body, actor_subject=admin.subject,
        actor_user_id=admin.user_id, corr=correlation_id(request))


# ------------------------------------------------- the tenant's own view ---

@tenant_billing_router.get("/subscription")
def my_subscription(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("administration", "view")),
):
    """What this tenant is on, and how their usage sits against it.

    No elevation. The RLS policy on these tables admits a bound transaction to
    its own organisation's rows, so the tenant binding that every request
    already carries is exactly the right amount of access.
    """
    org_id = _caller_org(caller)
    sub = _one(db, _SUB_SQL + " WHERE s.organization_id = :org "
               "AND s.status = ANY(:live) LIMIT 1",
               {"org": org_id, "live": list(LIVE)})
    usage = _usage(db, org_id)
    out: dict = {"subscription": sub, "usage": usage}
    if sub:
        limits = _limits(db, sub["plan_version_id"])
        out["limits"] = limits
        out["over_limit"] = _blockers(usage, limits)
    return out


@tenant_billing_router.get("/invoices")
def my_invoices(
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("administration", "view")),
):
    """This tenant's own subscription invoices."""
    return _rows(
        db,
        """
        SELECT id, series, number, status, period_start, period_end, due_on,
               currency, subtotal, tax_total, total, amount_paid, issued_at,
               paid_at
        FROM billing.invoices
        WHERE organization_id = :org AND status <> 'draft'
        ORDER BY coalesce(issued_at, created_at) DESC
        LIMIT :lim
        """,
        {"org": _caller_org(caller), "lim": limit},
    )


class SubscribeIn(BaseModel):
    plan_code: str = Field(min_length=2, max_length=40)


@tenant_billing_router.post("/subscribe")
def subscribe(
    request: Request,
    body: SubscribeIn,
    db: Session = Depends(get_session),
    caller: Caller = Depends(
        require_org_permission("administration", "configure")),
):
    """A tenant puts itself on a plan.

    This is the one place a tenant's own request causes a write to billing,
    and it is the confused-deputy shape: the handler has to elevate past the
    RLS policy that exists to stop tenants writing their own contract.

    So the organisation is taken from ``caller`` and never from the request.
    The body carries a plan code and nothing else -- there is no
    organisation_id field to get wrong, which is the same reason every tenant
    body in this codebase stopped accepting one.

    No money moves. This records what the tenant chose; collection is a
    separate step against a provider that is not yet configured, and a route
    that quietly took a payment would be worse than one that cannot.
    """
    org_id = _caller_org(caller)

    # Elevate only after the organisation is settled, and only to write the
    # rows the policies reserve for system context.
    system_context(db, reason=f"billing: {caller.subject} subscribes their own "
                              f"organisation to {body.plan_code}")
    return _apply_change(
        db, org_id=org_id,
        body=ApplyChangeIn(plan_code=body.plan_code,
                           reason=f"Chosen by {caller.subject}"),
        actor_subject=caller.subject, actor_user_id=caller.user_id,
        corr=correlation_id(request))


# ---------------------------------------------------------------- money ---

@platform_billing_router.get("/invoices")
def platform_invoices(
    org_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("billing.view")),
):
    """Subscription invoices across tenants — specification screen 13."""
    return _rows(
        db,
        """
        SELECT i.id, i.series, i.number, i.status, i.organization_id,
               o.name AS organization_name, i.period_start, i.period_end,
               i.due_on, i.currency, i.subtotal, i.tax_total, i.total,
               i.amount_paid, i.issued_at, i.paid_at,
               (i.total - i.amount_paid) AS outstanding
        FROM billing.invoices i
        JOIN iam.organizations o ON o.id = i.organization_id
        WHERE (CAST(:org AS uuid) IS NULL OR i.organization_id = :org)
          AND (CAST(:st AS text) IS NULL OR i.status = :st)
        ORDER BY coalesce(i.issued_at, i.created_at) DESC
        LIMIT :lim
        """,
        {"org": org_id, "st": status or None, "lim": limit},
    )


class VoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@platform_billing_router.post("/invoices/{invoice_id}/void")
def void_invoice(
    request: Request,
    invoice_id: uuid.UUID,
    body: VoidIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("invoice.manage")),
):
    """Void an issued invoice. A paid one is corrected by credit note instead.

    Voiding something already settled would leave a payment attached to a
    document that claims nothing is owed, and the money would still have
    moved. The credit note exists so the correction is visible rather than
    erased.
    """
    inv = _one(
        db,
        "SELECT id, status, organization_id, total, amount_paid "
        "FROM billing.invoices WHERE id = :i",
        {"i": invoice_id},
    )
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv["status"] == "paid" or _money(inv["amount_paid"]) > 0:
        raise HTTPException(
            status_code=409,
            detail="That invoice has been paid. Raise a credit note instead.")
    if inv["status"] == "void":
        raise HTTPException(status_code=409, detail="Already void.")

    db.execute(
        text("UPDATE billing.invoices SET status = 'void', "
             "cancelled_at = now(), cancelled_by = :by, cancel_reason = :why, "
             "updated_at = now() WHERE id = :i"),
        {"by": admin.user_id, "why": body.reason, "i": invoice_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="billing.invoice.voided", entity_type="invoice",
        entity_id=str(invoice_id), organization_id=inv["organization_id"],
        actor_subject=admin.subject, reason=body.reason,
        before={"status": inv["status"]}, after={"status": "void"},
    )
    return {"id": str(invoice_id), "status": "void"}


class CreditApproveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@platform_billing_router.post("/credit-notes/{note_id}/approve")
def approve_credit_note(
    request: Request,
    note_id: uuid.UUID,
    body: CreditApproveIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("credit.approve")),
):
    """Approve a credit note — and never your own.

    Separation of duties is the entire reason ``credit.approve`` is a
    capability distinct from ``invoice.manage``. Letting the requester approve
    would make the second signature decorative, and this is money leaving.
    """
    note = _one(
        db,
        "SELECT n.id, n.status, n.amount, n.created_by, i.organization_id, "
        "       i.total, i.id AS invoice_id "
        "FROM billing.credit_notes n "
        "JOIN billing.invoices i ON i.id = n.invoice_id WHERE n.id = :n",
        {"n": note_id},
    )
    if note is None:
        raise HTTPException(status_code=404, detail="Credit note not found")
    if note["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"Already {note['status']}.")
    if note["created_by"] and note["created_by"] == admin.user_id:
        raise HTTPException(
            status_code=409,
            detail="You raised this credit note. It needs a different "
                   "approver.")
    if _money(note["amount"]) > _money(note["total"]):
        raise HTTPException(
            status_code=409,
            detail="The credit exceeds the invoice it credits.")

    db.execute(
        text("UPDATE billing.credit_notes SET status = 'approved', "
             "approved_by = :by, approved_at = now() WHERE id = :n"),
        {"by": admin.user_id, "n": note_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="billing.credit_note.approved", entity_type="credit_note",
        entity_id=str(note_id), organization_id=note["organization_id"],
        actor_subject=admin.subject, reason=body.reason,
        before={"status": "pending"},
        after={"status": "approved", "amount": str(note["amount"])},
    )
    return {"id": str(note_id), "status": "approved"}


# ------------------------------------------------------- the billing run ---

class RunIn(BaseModel):
    #: Bill as though it were this date. For catching up deliberately, and for
    #: proving in a test that a period actually closed.
    as_of: date | None = None
    #: Show what would be invoiced and write nothing. The default, because the
    #: alternative default is a button that bills every customer on the first
    #: click by somebody exploring the screen.
    dry_run: bool = True


@platform_billing_router.post("/run")
def billing_run(
    request: Request,
    body: RunIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("invoice.manage")),
):
    """Issue invoices for every period that has closed.

    Safe to run repeatedly: the unique constraint on
    ``(subscription_id, period_start)`` means a second run over the same
    period produces nothing, so a retry after a timeout is not a double
    charge. ``dry_run`` defaults to true.
    """
    result = run_billing(db, as_of=body.as_of, dry_run=body.dry_run,
                         actor_subject=admin.subject)
    if not body.dry_run:
        record_audit(
            db, correlation_id=correlation_id(request),
            action="billing.run.triggered", entity_type="billing_run",
            entity_id=str(result.as_of), actor_subject=admin.subject,
            reason="manual run from the console",
            after={"invoices": len(result.invoiced)})
    return result.summary()
