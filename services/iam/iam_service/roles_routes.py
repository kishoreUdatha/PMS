"""Roles & Permission Matrix routes (US-041, mockup 041).

Manages roles and their module x action permission grid, plus access scope and
financial limits. RBAC: role.view / role.manage. All writes audited.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import schemas
from .audit import record_audit
from .authz import Caller, require_org_permission
from .database import get_session

roles_router = APIRouter(tags=["roles-matrix"], route_class=TransactionalRoute)

MODULES: list[tuple[str, str]] = [
    ("dashboard", "Dashboard"),
    ("front_desk", "Front Desk"),
    ("reservations", "Reservations"),
    ("rooms", "Rooms"),
    ("guests", "Guests"),
    ("housekeeping", "Housekeeping"),
    ("rates", "Rates"),
    ("distribution", "Distribution"),
    ("payments", "Payments"),
    ("pos", "POS"),
    ("reports", "Reports"),
    ("ai_center", "AI Center"),
    ("administration", "Administration"),
]
ACTIONS = ["view", "create", "edit", "cancel", "approve", "export", "configure"]


@roles_router.get("/roles/catalogue", response_model=schemas.RoleCatalogue)
def role_catalogue(
    caller: Caller = Depends(require_org_permission("role", "view")),
):
    return schemas.RoleCatalogue(
        modules=[schemas.ModuleDef(code=c, label=lbl) for c, lbl in MODULES],
        actions=ACTIONS,
    )


@roles_router.get("/roles/list", response_model=list[schemas.RoleListItem])
def roles_with_counts(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("role", "view")),
):
    rows = db.execute(
        text(
            """
            SELECT r.id, r.code, r.name,
                   (SELECT count(DISTINCT ra.membership_id)
                    FROM iam.role_assignments ra WHERE ra.role_id = r.id) AS user_count
            FROM iam.roles r
            WHERE r.organization_id = :org AND r.active = true
            ORDER BY r.name
            """
        ),
        {"org": caller.organization_id},
    ).mappings().all()
    return [schemas.RoleListItem(**r) for r in rows]


def _role_or_404(db: Session, caller: Caller, role_id: uuid.UUID):
    role = db.execute(
        text(
            "SELECT id, code, name, record_scope, property_scope, max_discount, "
            "max_refund FROM iam.roles WHERE id = :id AND organization_id = :org"
        ),
        {"id": role_id, "org": caller.organization_id},
    ).mappings().first()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@roles_router.get("/roles/{role_id}/matrix", response_model=schemas.RoleMatrixOut)
def get_role_matrix(
    role_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("role", "view")),
):
    role = _role_or_404(db, caller, role_id)
    # Which module.action permissions are granted to this role.
    granted = db.execute(
        text(
            """
            SELECT p.resource_code AS module, p.action_code AS action
            FROM iam.role_permissions rp
            JOIN iam.permissions p ON p.id = rp.permission_id
            WHERE rp.role_id = :rid
            """
        ),
        {"rid": role_id},
    ).all()
    granted_set = {(g.module, g.action) for g in granted}
    permissions = {
        code: {a: (code, a) in granted_set for a in ACTIONS}
        for code, _ in MODULES
    }
    user_count = db.execute(
        text(
            "SELECT count(DISTINCT membership_id) FROM iam.role_assignments "
            "WHERE role_id = :rid"
        ),
        {"rid": role_id},
    ).scalar_one()
    return schemas.RoleMatrixOut(
        id=role["id"],
        code=role["code"],
        name=role["name"],
        user_count=int(user_count),
        permissions=permissions,
        record_scope=role["record_scope"],
        property_scope=role["property_scope"],
        max_discount=role["max_discount"],
        max_refund=role["max_refund"],
    )


@roles_router.put("/roles/{role_id}/matrix", response_model=schemas.RoleMatrixOut)
def save_role_matrix(
    role_id: uuid.UUID,
    body: schemas.RoleMatrixUpdate,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("role", "manage")),
):
    """Replace the role's permission grid + scope/limits (US-041), audited."""
    role = _role_or_404(db, caller, role_id)

    before = db.execute(
        text(
            """
            SELECT count(*) FROM iam.role_permissions WHERE role_id = :rid
            """
        ),
        {"rid": role_id},
    ).scalar_one()

    # Rebuild role_permissions to exactly match the submitted grid.
    db.execute(
        text("DELETE FROM iam.role_permissions WHERE role_id = :rid"),
        {"rid": role_id},
    )
    inserted = 0
    for module, actions in body.permissions.items():
        for action, allowed in actions.items():
            if not allowed:
                continue
            perm = db.execute(
                text(
                    "SELECT id FROM iam.permissions WHERE resource_code = :m "
                    "AND action_code = :a"
                ),
                {"m": module, "a": action},
            ).scalar()
            if perm is None:
                continue
            db.execute(
                text(
                    """
                    INSERT INTO iam.role_permissions
                        (id, role_id, permission_id, record_scope)
                    VALUES (gen_random_uuid(), :rid, :pid, :scope)
                    ON CONFLICT (role_id, permission_id) DO NOTHING
                    """
                ),
                {"rid": role_id, "pid": perm, "scope": body.record_scope},
            )
            inserted += 1

    db.execute(
        text(
            """
            UPDATE iam.roles
            SET record_scope = :rs, property_scope = :ps,
                max_discount = :md, max_refund = :mr, version = version + 1
            WHERE id = :rid
            """
        ),
        {
            "rs": body.record_scope,
            "ps": body.property_scope,
            "md": body.max_discount,
            "mr": body.max_refund,
            "rid": role_id,
        },
    )

    record_audit(
        db,
        action="role.matrix.update",
        entity_type="role",
        entity_id=str(role_id),
        organization_id=caller.organization_id,
        actor_subject=caller.subject,
        before={"granted_permissions": int(before)},
        after={
            "granted_permissions": inserted,
            "record_scope": body.record_scope,
            "property_scope": body.property_scope,
            "max_discount": str(body.max_discount) if body.max_discount is not None else None,
            "max_refund": str(body.max_refund) if body.max_refund is not None else None,
        },
        reason=body.reason,
    )
    return get_role_matrix(role_id, db, caller)


@roles_router.post(
    "/roles/create",
    response_model=schemas.RoleListItem,
    status_code=status.HTTP_201_CREATED,
)
def create_role(
    body: schemas.RoleCreate2,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("role", "manage")),
):
    code = (body.code or body.name).upper().replace(" ", "_")[:50]
    rid = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO iam.roles (id, organization_id, code, name, active)
                VALUES (:id, :org, :code, :name, true)
                """
            ),
            {"id": rid, "org": caller.organization_id, "code": code, "name": body.name},
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=409, detail="Role code already exists") from exc
    record_audit(
        db, action="role.create", entity_type="role", entity_id=str(rid),
        organization_id=caller.organization_id, actor_subject=caller.subject,
        after={"code": code, "name": body.name},
    )
    return schemas.RoleListItem(id=rid, code=code, name=body.name, user_count=0)


@roles_router.post(
    "/roles/{role_id}/clone",
    response_model=schemas.RoleListItem,
    status_code=status.HTTP_201_CREATED,
)
def clone_role(
    role_id: uuid.UUID,
    body: schemas.RoleClone,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("role", "manage")),
):
    src = _role_or_404(db, caller, role_id)
    code = body.name.upper().replace(" ", "_")[:50]
    new_id = uuid.uuid4()
    try:
        db.execute(
            text(
                """
                INSERT INTO iam.roles (id, organization_id, code, name, active,
                    record_scope, property_scope, max_discount, max_refund)
                VALUES (:id, :org, :code, :name, true, :rs, :ps, :md, :mr)
                """
            ),
            {
                "id": new_id, "org": caller.organization_id, "code": code,
                "name": body.name, "rs": src["record_scope"],
                "ps": src["property_scope"], "md": src["max_discount"],
                "mr": src["max_refund"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=409, detail="Role code already exists") from exc
    # Copy permissions.
    db.execute(
        text(
            """
            INSERT INTO iam.role_permissions (id, role_id, permission_id, record_scope)
            SELECT gen_random_uuid(), :new, permission_id, record_scope
            FROM iam.role_permissions WHERE role_id = :src
            """
        ),
        {"new": new_id, "src": role_id},
    )
    record_audit(
        db, action="role.clone", entity_type="role", entity_id=str(new_id),
        organization_id=caller.organization_id, actor_subject=caller.subject,
        after={"cloned_from": str(role_id), "name": body.name},
    )
    return schemas.RoleListItem(id=new_id, code=code, name=body.name, user_count=0)
