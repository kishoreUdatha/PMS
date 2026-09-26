"""IAM API routes.

Basic CRUD for organizations, properties, and users to bootstrap the tenant
model. Fine-grained authorization (scoped permissions, §2) is layered on next;
these endpoints establish the data model and are gateway-guarded.
"""

from __future__ import annotations

import secrets
import uuid

from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models, schemas
from .audit import record_audit
from .authz import (
    assert_property_in_org, require_property_permission,
    Caller, get_caller, require_org_permission, require_permission,
)
from .database import get_session
from .settings import settings

router = APIRouter(route_class=TransactionalRoute)


# ---- Organizations ----
@router.post(
    "/organizations",
    response_model=schemas.OrganizationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["organizations"],
)
def create_organization(
    body: schemas.OrganizationCreate,
    db: Session = Depends(get_session),
    caller: Caller = Depends(get_caller),
) -> models.Organization:
    """Create a tenant.

    An organisation *is* a tenant, so creating one from a signed-in session
    would let any customer spawn tenants on the platform. The real path is
    ``/auth/sign-up``, which makes the organisation, its property and its
    owner together. This is left for seeding and local tooling, and refuses
    outside development.
    """
    if settings.environment == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organisations are created by signing up, not through this "
                   "endpoint.",
        )
    org = models.Organization(name=body.name)
    db.add(org)
    db.flush()
    return org


@router.get(
    "/organizations",
    response_model=list[schemas.OrganizationOut],
    tags=["organizations"],
)
def list_organizations(
    db: Session = Depends(get_session),
    caller: Caller = Depends(get_caller),
):
    """The caller's own organisation, and only that.

    This returned every organisation on the platform, to anybody, with no
    authentication at all — the tenant list of a multi-tenant product.
    """
    if caller.organization_id is None:
        return []
    return db.scalars(
        select(models.Organization)
        .where(models.Organization.id == caller.organization_id)
    ).all()


def _new_property_code(db: Session) -> str:
    """A six-digit code nobody else on the platform holds.

    Generated rather than taken from the caller. The code has to be unique
    across every tenant, because sign-in looks it up before anyone knows which
    tenant is asking — and a code somebody chooses cannot be checked for
    uniqueness without telling them something about the properties that
    already exist.

    Random inside the range rather than sequential: consecutive codes would
    publish how many properties are on the platform, and make the neighbouring
    tenant's code a matter of adding one.
    """
    for _ in range(20):
        candidate = str(secrets.randbelow(900_000) + 100_000)
        taken = db.execute(
            text("SELECT 1 FROM iam.properties WHERE code = :c"),
            {"c": candidate},
        ).first()
        if taken is None:
            return candidate
    # 900,000 values and twenty draws: reaching here means the space is
    # genuinely crowded, which is a capacity problem rather than a bad draw.
    raise HTTPException(
        status_code=503,
        detail="Could not allocate a property code. Try again.",
    )


# ---- Properties ----
@router.post(
    "/properties",
    response_model=schemas.PropertyOut,
    status_code=status.HTTP_201_CREATED,
    tags=["properties"],
)
def create_property(
    body: schemas.PropertyCreate,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("property", "create")),
) -> models.Property:
    """Add a property to the caller's own organisation.

    ``organization_id`` in the body is ignored. It was taken at face value,
    unauthenticated, so anyone could create a property inside somebody else's
    tenant — and then reach its data through every endpoint that scopes by
    property.
    """
    if caller.organization_id is None:
        raise HTTPException(
            status_code=403, detail="You do not belong to an organisation.")
    org = db.get(models.Organization, caller.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    prop = models.Property(
        organization_id=caller.organization_id,
        code=_new_property_code(db),
        name=body.name,
        timezone=body.timezone,
        currency=body.currency,
        checkin_time=body.checkin_time,
        checkout_time=body.checkout_time,
    )
    db.add(prop)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="That property could not be created."
        ) from exc
    return prop


@router.get(
    "/properties",
    response_model=list[schemas.PropertyOut],
    tags=["properties"],
)
def list_properties(
    db: Session = Depends(get_session),
    caller: Caller = Depends(get_caller),
):
    """The properties of the caller's own organisation.

    This took ``organization_id`` as an optional filter and returned
    *everything* when it was left off — unauthenticated. The property switcher
    calls it, so one resort's staff were shown another resort in their own
    menu and could select it. The filter is gone: a caller sees their tenant,
    and there is no parameter that says otherwise.
    """
    if caller.organization_id is None:
        return []
    return db.scalars(
        select(models.Property)
        .where(models.Property.organization_id == caller.organization_id)
    ).all()


# ---- User Management (US-039) — org-scoped, RBAC-guarded, audited ----
@router.get("/users", response_model=list[schemas.UserListItem], tags=["users"])
def list_users(
    query: str | None = None,
    status_filter: str | None = None,
    role_id: str | None = None,
    property_id: str | None = None,
    department_id: str | None = None,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "view")),
):
    """List/search users in the caller's org with rich columns (US-039-01).

    Returns employee code, department, properties, roles, last login, MFA and
    active session count per user, joined from the IAM tables. Supports
    server-side filters: query (name/subject), status, role, property, department.
    Empty-string filters are treated as "no filter".
    """
    # Only when a property is actually named. Unlike every other guarded
    # handler, this one's property_id is a filter rather than the subject of
    # the request: absent means "all of my org's users", which is already
    # scoped by the query below. Asserting unconditionally would refuse the
    # unfiltered call, which is the common one.
    if property_id:
        assert_property_in_org(db, caller, property_id)
    # Normalize empty strings to None (defensive against blank query params).
    query = query or None
    status_filter = status_filter or None
    role_id = role_id or None
    property_id = property_id or None
    department_id = department_id or None
    stored_status = (
        "inactive" if status_filter == "suspended" else status_filter
    )
    sql = """
        SELECT u.id, u.identity_provider, u.subject_id, u.display_name,
               u.status, u.version, u.mfa_status,
               to_char(u.last_login_at, 'DD Mon YYYY HH24:MI') AS last_login_at,
               m.status AS membership_status,
               e.employee_code,
               d.name AS department,
               COALESCE((
                   SELECT array_agg(DISTINCT r.name)
                   FROM iam.role_assignments ra
                   JOIN iam.roles r ON r.id = ra.role_id
                   WHERE ra.membership_id = m.id
               ), ARRAY[]::varchar[]) AS roles,
               COALESCE((
                   SELECT array_agg(DISTINCT p.name)
                   FROM iam.role_assignments ra
                   JOIN iam.properties p ON p.id = ra.property_id
                   WHERE ra.membership_id = m.id AND ra.property_id IS NOT NULL
               ), ARRAY[]::varchar[]) AS properties,
               (SELECT count(*) FROM iam.login_sessions s
                 WHERE s.user_id = u.id AND s.revoked_at IS NULL
                   AND (s.expires_at IS NULL OR s.expires_at > now())
               ) AS active_sessions
        FROM iam.users u
        JOIN iam.memberships m ON m.user_id = u.id
        LEFT JOIN iam.employees e ON e.membership_id = m.id
        LEFT JOIN iam.departments d ON d.id = e.department_id
        WHERE m.organization_id = :org
    """
    params: dict = {"org": caller.organization_id}
    if query:
        sql += " AND (u.display_name ILIKE :q OR u.subject_id ILIKE :q)"
        params["q"] = f"%{query}%"
    if stored_status:
        sql += " AND u.status = :st"
        params["st"] = stored_status
    if department_id:
        sql += " AND e.department_id = :dept"
        params["dept"] = department_id
    if role_id:
        sql += (
            " AND EXISTS (SELECT 1 FROM iam.role_assignments ra "
            "WHERE ra.membership_id = m.id AND ra.role_id = :role)"
        )
        params["role"] = role_id
    if property_id:
        sql += (
            " AND EXISTS (SELECT 1 FROM iam.role_assignments ra "
            "WHERE ra.membership_id = m.id AND ra.property_id = :prop)"
        )
        params["prop"] = property_id
    sql += " ORDER BY e.employee_code NULLS LAST, u.display_name LIMIT 200"
    rows = db.execute(text(sql), params).mappings().all()
    return [schemas.UserListItem(**r) for r in rows]


@router.get("/departments", response_model=list[schemas.DepartmentOut], tags=["users"])
def list_departments(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "view")),
):
    rows = db.execute(
        text(
            "SELECT id, code, name FROM iam.departments WHERE organization_id = :org "
            "ORDER BY name"
        ),
        {"org": caller.organization_id},
    ).mappings().all()
    return [schemas.DepartmentOut(**r) for r in rows]


@router.get("/roles", response_model=list[schemas.RoleOut], tags=["users"])
def list_roles(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "view")),
):
    rows = db.execute(
        text(
            "SELECT id, code, name FROM iam.roles WHERE organization_id = :org "
            "AND active = true ORDER BY name"
        ),
        {"org": caller.organization_id},
    ).mappings().all()
    return [schemas.RoleOut(**r) for r in rows]


@router.get("/users/stats", response_model=schemas.UserStatsOut, tags=["users"])
def user_stats(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "view")),
):
    """User counts by status for the caller's organization (US-039-01 KPIs).

    active_sessions is a placeholder (0) until the sessions feature exists.
    """
    row = db.execute(
        text(
            """
            SELECT
              count(*) FILTER (WHERE u.status = 'active')   AS active,
              count(*) FILTER (WHERE u.status = 'invited')  AS invited,
              count(*) FILTER (WHERE u.status = 'inactive') AS suspended
            FROM iam.users u
            JOIN iam.memberships m ON m.user_id = u.id
            WHERE m.organization_id = :org
            """
        ),
        {"org": caller.organization_id},
    ).mappings().first()
    sessions = db.execute(
        text(
            """
            SELECT count(*) FROM iam.login_sessions s
            WHERE s.organization_id = :org AND s.revoked_at IS NULL
              AND (s.expires_at IS NULL OR s.expires_at > now())
            """
        ),
        {"org": caller.organization_id},
    ).scalar_one()
    return schemas.UserStatsOut(
        active=row["active"],
        invited=row["invited"],
        suspended=row["suspended"],
        active_sessions=int(sessions),
    )


@router.post(
    "/users",
    response_model=schemas.UserListItem,
    status_code=status.HTTP_201_CREATED,
    tags=["users"],
)
def create_user(
    body: schemas.UserManagementCreate,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "create")),
):
    """Create a user and add them to the caller's organization (US-039-02)."""
    user = models.User(
        identity_provider=body.identity_provider,
        subject_id=body.subject_id,
        display_name=body.display_name,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="User with provider+subject already exists"
        ) from exc

    membership = models.Membership(
        organization_id=caller.organization_id, user_id=user.id, status="active"
    )
    db.add(membership)
    db.flush()

    record_audit(
        db,
        action="user.create",
        entity_type="user",
        entity_id=str(user.id),
        organization_id=caller.organization_id,
        actor_subject=caller.subject,
        after={
            "subject_id": user.subject_id,
            "display_name": user.display_name,
            "identity_provider": user.identity_provider,
        },
    )
    return schemas.UserListItem(
        id=user.id,
        identity_provider=user.identity_provider,
        subject_id=user.subject_id,
        display_name=user.display_name,
        status=user.status,
        version=user.version,
        membership_status="active",
    )


def _org_user_or_404(db: Session, caller: Caller, user_id: uuid.UUID) -> models.User:
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    member = db.execute(
        text(
            "SELECT 1 FROM iam.memberships WHERE user_id = :uid "
            "AND organization_id = :org"
        ),
        {"uid": user_id, "org": caller.organization_id},
    ).first()
    if member is None:
        raise HTTPException(status_code=403, detail="User not in your organization")
    return user


@router.patch(
    "/users/{user_id}",
    response_model=schemas.UserListItem,
    tags=["users"],
)
def update_user(
    user_id: uuid.UUID,
    body: schemas.UserManagementUpdate,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "update")),
):
    """Edit a user's display name with optimistic locking + audit (US-039-02)."""
    user = _org_user_or_404(db, caller, user_id)
    if user.version != body.version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected {user.version}, got {body.version}.",
        )
    before = {"display_name": user.display_name}
    user.display_name = body.display_name
    user.version += 1
    db.flush()
    record_audit(
        db,
        action="user.update",
        entity_type="user",
        entity_id=str(user_id),
        organization_id=caller.organization_id,
        actor_subject=caller.subject,
        before=before,
        after={"display_name": user.display_name},
        reason=body.reason,
    )
    return _user_list_item(db, user, caller)


@router.post(
    "/users/{user_id}/deactivate",
    response_model=schemas.UserListItem,
    tags=["users"],
)
def deactivate_user(
    user_id: uuid.UUID,
    body: schemas.UserActiveToggle,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "deactivate")),
):
    """Deactivate a user (US-039-02/03). Cannot deactivate yourself."""
    user = _org_user_or_404(db, caller, user_id)
    if str(user.id) == str(caller.user_id):
        raise HTTPException(status_code=409, detail="You cannot deactivate yourself")
    return _set_user_status(db, caller, user, "inactive", body.reason)


@router.post(
    "/users/{user_id}/activate",
    response_model=schemas.UserListItem,
    tags=["users"],
)
def activate_user(
    user_id: uuid.UUID,
    body: schemas.UserActiveToggle,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "deactivate")),
):
    """Reactivate a user (US-039-02)."""
    user = _org_user_or_404(db, caller, user_id)
    return _set_user_status(db, caller, user, "active", body.reason)


def _set_user_status(
    db: Session, caller: Caller, user: models.User, new_status: str, reason: str | None
) -> schemas.UserListItem:
    before = {"status": user.status}
    user.status = new_status
    user.version += 1
    db.flush()
    record_audit(
        db,
        action=f"user.{'deactivate' if new_status == 'inactive' else 'activate'}",
        entity_type="user",
        entity_id=str(user.id),
        organization_id=caller.organization_id,
        actor_subject=caller.subject,
        before=before,
        after={"status": user.status},
        reason=reason,
    )
    return _user_list_item(db, user, caller)


def _user_list_item(
    db: Session, user: models.User, caller: Caller
) -> schemas.UserListItem:
    ms = db.execute(
        text(
            "SELECT status FROM iam.memberships WHERE user_id = :uid "
            "AND organization_id = :org"
        ),
        {"uid": user.id, "org": caller.organization_id},
    ).scalar()
    return schemas.UserListItem(
        id=user.id,
        identity_provider=user.identity_provider,
        subject_id=user.subject_id,
        display_name=user.display_name,
        status=user.status,
        version=user.version,
        membership_status=ms,
    )



@router.post(
    "/users/{user_id}/revoke-sessions",
    tags=["users"],
)
def revoke_sessions(
    user_id: uuid.UUID,
    body: schemas.UserActiveToggle,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "deactivate")),
):
    """Revoke all active login sessions for a user (US-039-02/03), audited."""
    user = _org_user_or_404(db, caller, user_id)
    revoked = db.execute(
        text(
            """
            UPDATE iam.login_sessions SET revoked_at = now()
            WHERE user_id = :uid AND revoked_at IS NULL
            """
        ),
        {"uid": user.id},
    ).rowcount
    record_audit(
        db,
        action="user.revoke_sessions",
        entity_type="user",
        entity_id=str(user_id),
        organization_id=caller.organization_id,
        actor_subject=caller.subject,
        after={"revoked_sessions": revoked},
        reason=body.reason,
    )
    return {"user_id": str(user_id), "revoked_sessions": revoked}


@router.get(
    "/users/{user_id}/activity",
    response_model=list[schemas.ActivityOut],
    tags=["users"],
)
def user_activity(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "view")),
):
    """Recent audit activity for a user (US-039 View Activity)."""
    _org_user_or_404(db, caller, user_id)
    rows = db.execute(
        text(
            """
            SELECT action, entity_type, reason,
                   to_char(occurred_at, 'DD Mon YYYY HH24:MI') AS occurred_at
            FROM iam.audit_events
            WHERE entity_type = 'user' AND entity_id = :eid
            ORDER BY occurred_at DESC LIMIT 50
            """
        ),
        {"eid": str(user_id)},
    ).mappings().all()
    return [schemas.ActivityOut(**r) for r in rows]


# ---- Invitations (US-040 Invite or Edit User Access) ----
@router.post(
    "/invitations",
    response_model=schemas.InvitationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["invitations"],
)
def create_invitation(
    body: schemas.InvitationCreate,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "create")),
):
    """Invite a user (US-040): create the invited user with roles + access.

    Creates the user (status 'invited'), membership, employee record, role
    assignments, an approval policy (discount/refund limits), and the invitation
    + grants — all in one transaction, audited. The property must belong to the
    caller's organization.
    """
    # The property is named in the body, where guard_request_tenancy
    # cannot see it.
    require_property_permission(db, caller, body.property_id,
                                "user", "create")
    org = caller.organization_id
    prop = db.execute(
        text(
            "SELECT id FROM iam.properties WHERE id = :p AND organization_id = :org"
        ),
        {"p": body.property_id, "org": org},
    ).first()
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found in your org")

    # Derive a subject from the email local-part, with a suffix so two people
    # called priya@ at different domains cannot collide on it.
    subject = f"{body.email.split('@')[0].lower()}-{uuid.uuid4().hex[:6]}"

    user = models.User(
        identity_provider="local",
        subject_id=subject,
        display_name=body.full_name,
        status="invited",
        # The address is the whole point of an invitation: it is where the
        # welcome link goes and what the person types to sign in. Leaving it
        # off the user meant an invited person could be neither written to nor
        # signed in as -- the invitation existed and led nowhere.
        email=body.email.strip().lower(),
        phone=(body.phone or "").strip() or None,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A user with this email/subject already exists"
        ) from exc

    membership = models.Membership(
        organization_id=org, user_id=user.id, status="invited"
    )
    db.add(membership)
    db.flush()

    # Employee record (employee code + department).
    if body.employee_code or body.department_id:
        db.execute(
            text(
                """
                INSERT INTO iam.employees
                    (id, organization_id, membership_id, employee_code, department_id)
                VALUES (gen_random_uuid(), :org, :mem, :code, :dept)
                """
            ),
            {
                "org": org,
                "mem": membership.id,
                "code": body.employee_code or subject,
                "dept": body.department_id,
            },
        )

    # Role assignments (property-scoped) for each selected role.
    for role_id in body.role_ids:
        db.execute(
            text(
                """
                INSERT INTO iam.role_assignments
                    (id, membership_id, role_id, property_id, scope_type)
                VALUES (gen_random_uuid(), :mem, :role, :prop, 'property')
                """
            ),
            {"mem": membership.id, "role": role_id, "prop": body.property_id},
        )

    # Approval limits (discount/refund) are captured on the invitation record
    # below and applied to the membership's approval policy on acceptance
    # (acceptance flow is a later story). No approval_policies write here yet.

    # Invitation record.
    inv_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO iam.invitations
                (id, organization_id, property_id, email, full_name, phone,
                 employee_code, department_id, mfa_required, temporary_access,
                 access_expiry, max_discount_approval, refund_approval, status,
                 created_by, created_user_id)
            VALUES (:id, :org, :prop, :email, :name, :phone, :code, :dept,
                    :mfa, :temp, :exp, :disc, :refund, 'invited', :by, :uid)
            """
        ),
        {
            "id": inv_id,
            "org": org,
            "prop": body.property_id,
            "email": body.email,
            "name": body.full_name,
            "phone": body.phone,
            "code": body.employee_code,
            "dept": body.department_id,
            "mfa": body.mfa_required,
            "temp": body.temporary_access,
            "exp": body.access_expiry,
            "disc": body.max_discount_approval,
            "refund": body.refund_approval,
            "by": caller.subject,
            "uid": user.id,
        },
    )
    for role_id in body.role_ids:
        db.execute(
            text(
                """
                INSERT INTO iam.invitation_grants
                    (id, invitation_id, role_id, scope_type)
                VALUES (gen_random_uuid(), :inv, :role, 'property')
                """
            ),
            {"inv": inv_id, "role": role_id},
        )

    # Set MFA requirement on the user record.
    if body.mfa_required:
        db.execute(
            text("UPDATE iam.users SET mfa_status = 'pending' WHERE id = :uid"),
            {"uid": user.id},
        )

    record_audit(
        db,
        action="invitation.create",
        entity_type="invitation",
        entity_id=str(inv_id),
        organization_id=org,
        property_id=body.property_id,
        actor_subject=caller.subject,
        after={
            "email": body.email,
            "full_name": body.full_name,
            "roles": [str(r) for r in body.role_ids],
            "mfa_required": body.mfa_required,
            "temporary_access": body.temporary_access,
            "max_discount_approval": str(body.max_discount_approval)
            if body.max_discount_approval is not None
            else None,
            "refund_approval": body.refund_approval,
        },
    )

    return schemas.InvitationOut(
        id=inv_id,
        email=body.email,
        full_name=body.full_name,
        status="invited",
        created_user_id=user.id,
        role_count=len(body.role_ids),
    )


@router.get(
    "/invitations",
    response_model=list[schemas.InvitationOut],
    tags=["invitations"],
)
def list_invitations(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "view")),
):
    rows = db.execute(
        text(
            """
            SELECT i.id, i.email, i.full_name, i.status, i.created_user_id,
                   (SELECT count(*) FROM iam.invitation_grants g
                     WHERE g.invitation_id = i.id) AS role_count
            FROM iam.invitations i
            WHERE i.organization_id = :org
            ORDER BY i.created_at DESC LIMIT 100
            """
        ),
        {"org": caller.organization_id},
    ).mappings().all()
    return [schemas.InvitationOut(**r) for r in rows]


# ---- User Access (US-040 Edit User Access) ----
@router.get(
    "/users/{user_id}/access",
    response_model=schemas.UserAccessOut,
    tags=["invitations"],
)
def get_user_access(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "view")),
):
    """Return a user's current access for the Edit User Access screen (US-040)."""
    user = _org_user_or_404(db, caller, user_id)
    mem = db.execute(
        text(
            "SELECT id FROM iam.memberships WHERE user_id = :uid "
            "AND organization_id = :org"
        ),
        {"uid": user_id, "org": caller.organization_id},
    ).scalar_one()

    emp = db.execute(
        text(
            "SELECT employee_code, department_id FROM iam.employees "
            "WHERE membership_id = :mem"
        ),
        {"mem": mem},
    ).first()

    assignments = db.execute(
        text(
            "SELECT DISTINCT role_id, property_id FROM iam.role_assignments "
            "WHERE membership_id = :mem"
        ),
        {"mem": mem},
    ).all()
    role_ids = [a.role_id for a in assignments]
    property_id = next((a.property_id for a in assignments if a.property_id), None)

    inv = db.execute(
        text(
            """
            SELECT temporary_access, access_expiry, max_discount_approval,
                   refund_approval
            FROM iam.invitations WHERE created_user_id = :uid
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"uid": user_id},
    ).first()

    return schemas.UserAccessOut(
        user_id=user.id,
        full_name=user.display_name,
        email=user.subject_id,
        phone=None,
        employee_code=emp.employee_code if emp else None,
        department_id=emp.department_id if emp else None,
        property_id=property_id,
        role_ids=role_ids,
        mfa_required=user.mfa_status != "disabled",
        temporary_access=bool(inv.temporary_access) if inv else False,
        access_expiry=inv.access_expiry if inv else None,
        max_discount_approval=inv.max_discount_approval if inv else None,
        refund_approval=bool(inv.refund_approval) if inv else False,
        version=user.version,
    )


@router.put(
    "/users/{user_id}/access",
    response_model=schemas.UserAccessOut,
    tags=["invitations"],
)
def update_user_access(
    user_id: uuid.UUID,
    body: schemas.UserAccessUpdate,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("user", "update")),
):
    """Update a user's access (US-040 Edit): name, department, roles, settings.

    Replaces the property-scoped role assignment set. Optimistic-locked on the
    user version. Audited before/after.
    """
    # The property is named in the body, where guard_request_tenancy
    # cannot see it.
    require_property_permission(db, caller, body.property_id,
                                "user", "update")
    user = _org_user_or_404(db, caller, user_id)
    if user.version != body.version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected {user.version}, got {body.version}.",
        )
    mem = db.execute(
        text(
            "SELECT id FROM iam.memberships WHERE user_id = :uid "
            "AND organization_id = :org"
        ),
        {"uid": user_id, "org": caller.organization_id},
    ).scalar_one()

    # Capture before-state for audit.
    before_roles = [
        str(r)
        for r in db.execute(
            text(
                "SELECT DISTINCT role_id FROM iam.role_assignments "
                "WHERE membership_id = :mem"
            ),
            {"mem": mem},
        ).scalars()
    ]
    before = {"display_name": user.display_name, "roles": before_roles,
              "mfa": user.mfa_status}

    # Update user + employee.
    user.display_name = body.full_name
    user.mfa_status = "pending" if body.mfa_required else "disabled"
    user.version += 1
    db.flush()

    db.execute(
        text(
            """
            INSERT INTO iam.employees
                (id, organization_id, membership_id, employee_code, department_id)
            VALUES (gen_random_uuid(), :org, :mem, :code, :dept)
            ON CONFLICT (membership_id) DO UPDATE
                SET employee_code = COALESCE(EXCLUDED.employee_code,
                        iam.employees.employee_code),
                    department_id = EXCLUDED.department_id
            """
        ),
        {
            "org": caller.organization_id,
            "mem": mem,
            "code": body.employee_code or user.subject_id,
            "dept": body.department_id,
        },
    )

    # Replace property-scoped role assignments with the provided set.
    db.execute(
        text(
            "DELETE FROM iam.role_assignments WHERE membership_id = :mem "
            "AND property_id = :prop"
        ),
        {"mem": mem, "prop": body.property_id},
    )
    for role_id in body.role_ids:
        db.execute(
            text(
                """
                INSERT INTO iam.role_assignments
                    (id, membership_id, role_id, property_id, scope_type)
                VALUES (gen_random_uuid(), :mem, :role, :prop, 'property')
                """
            ),
            {"mem": mem, "role": role_id, "prop": body.property_id},
        )

    record_audit(
        db,
        action="user.access.update",
        entity_type="user",
        entity_id=str(user_id),
        organization_id=caller.organization_id,
        property_id=body.property_id,
        actor_subject=caller.subject,
        before=before,
        after={
            "display_name": user.display_name,
            "roles": [str(r) for r in body.role_ids],
            "mfa": user.mfa_status,
        },
        reason=body.reason,
    )

    return schemas.UserAccessOut(
        user_id=user.id,
        full_name=user.display_name,
        email=user.subject_id,
        phone=body.phone,
        employee_code=body.employee_code,
        department_id=body.department_id,
        property_id=body.property_id,
        role_ids=body.role_ids,
        mfa_required=body.mfa_required,
        temporary_access=body.temporary_access,
        access_expiry=body.access_expiry,
        max_discount_approval=body.max_discount_approval,
        refund_approval=body.refund_approval,
        version=user.version,
    )


# ---- Property Settings (US-026) ----
@router.get(
    "/properties/{property_id}/settings",
    response_model=schemas.PropertySettingsOut,
    tags=["property-settings"],
)
def get_property_settings(
    property_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_permission("property", "view")),
):
    """View property configuration (US-026-01). Permission: property.view."""
    assert_property_in_org(db, caller, property_id)
    prop = db.get(models.Property, property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    # Record scope: caller's org must own the property (deny cross-tenant).
    if caller.organization_id != prop.organization_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")
    return prop


@router.put(
    "/properties/{property_id}/settings",
    response_model=schemas.PropertySettingsOut,
    tags=["property-settings"],
)
def update_property_settings(
    property_id: uuid.UUID,
    body: schemas.PropertySettingsUpdate,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_permission("property", "update")),
):
    """Update property configuration (US-026-02) with optimistic locking + audit.

    Permission: property.update. Returns 409 on version conflict. Writes an
    append-only audit event with before/after in the same transaction (US-026-03).
    """
    assert_property_in_org(db, caller, property_id)
    prop = db.get(models.Property, property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    if caller.organization_id != prop.organization_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")

    # Optimistic concurrency: reject stale writes.
    if prop.version != body.version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Version conflict: expected {prop.version}, got {body.version}. "
                "Reload and retry."
            ),
        )

    before = {
        "name": prop.name,
        "timezone": prop.timezone,
        "currency": prop.currency,
        "checkin_time": prop.checkin_time,
        "checkout_time": prop.checkout_time,
        "address": prop.address,
    }

    prop.name = body.name
    prop.timezone = body.timezone
    prop.currency = body.currency
    prop.checkin_time = body.checkin_time
    prop.checkout_time = body.checkout_time
    prop.address = body.address
    prop.version = prop.version + 1

    after = {
        "name": prop.name,
        "timezone": prop.timezone,
        "currency": prop.currency,
        "checkin_time": prop.checkin_time,
        "checkout_time": prop.checkout_time,
        "address": prop.address,
    }

    db.flush()
    record_audit(
        db,
        action="property.settings.update",
        entity_type="property",
        entity_id=str(property_id),
        organization_id=prop.organization_id,
        property_id=property_id,
        actor_subject=caller.subject,
        before=before,
        after=after,
        reason=body.reason,
    )
    return prop


@router.get(
    "/properties/{property_id}/audit",
    tags=["property-settings"],
)
def property_audit(
    property_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_permission("audit", "view")),
):
    """Recent audit events for a property (US-043 / US-026-03 evidence)."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT action, entity_type, entity_id, actor_subject, reason,
                   redacted_before, redacted_after, occurred_at
            FROM iam.audit_events
            WHERE property_id = :prop
            ORDER BY occurred_at DESC LIMIT 50
            """
        ),
        {"prop": property_id},
    ).mappings().all()
    return list(rows)


# ---- Module entitlements --------------------------------------------------
#: Modules a property can be granted, and what to call them in the UI.
#:
#: A fixed vocabulary rather than free text. ``module_code`` is a varchar, and
#: an endpoint that wrote whatever it was given would let a typo ("bookingengine")
#: look like a successful switch-on while the booking engine stayed dark.
KNOWN_MODULES: dict[str, str] = {
    "booking_engine": "Online booking engine",
}


def _property_in_caller_org(db: Session, caller: Caller, property_id: uuid.UUID):
    """The property, if it belongs to the caller's tenant. 404 otherwise.

    404 and not 403: whether a property id exists in somebody else's
    organisation is not information this endpoint should confirm.
    """
    row = db.execute(
        text("SELECT id, organization_id, name FROM iam.properties "
             "WHERE id = :p AND organization_id = :o"),
        {"p": property_id, "o": caller.organization_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such property.")
    return row


@router.get(
    "/properties/{property_id}/modules",
    response_model=list[schemas.ModuleOut],
    tags=["properties"],
)
def list_property_modules(
    property_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("distribution", "view")),
):
    """Which modules this property has.

    Every known module is listed, entitled or not, so the caller sees what is
    available rather than only what is already on.
    """
    assert_property_in_org(db, caller, property_id)
    _property_in_caller_org(db, caller, property_id)
    rows = {
        r["module_code"]: r
        for r in db.execute(
            text("SELECT module_code, enabled, enabled_at "
                 "FROM iam.property_modules WHERE property_id = :p"),
            {"p": property_id},
        ).mappings().all()
    }
    return [
        schemas.ModuleOut(
            module_code=code,
            label=label,
            enabled=bool(rows.get(code, {}).get("enabled", False)),
            enabled_at=rows.get(code, {}).get("enabled_at"),
        )
        for code, label in sorted(KNOWN_MODULES.items())
    ]


@router.put(
    "/properties/{property_id}/modules/{module_code}",
    response_model=schemas.ModuleOut,
    tags=["properties"],
)
def set_property_module(
    property_id: uuid.UUID,
    module_code: str,
    body: schemas.ModuleIn,
    db: Session = Depends(get_session),
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
):
    """Grant or withdraw a module for one property.

    Gated on ``distribution:configure`` -- distribution is the hotel word for
    the channels a property sells through, which is exactly what this is. Not
    ``property:update``: that is addresses and phone numbers, and whoever
    corrects a phone number should not be able to put the hotel on sale to the
    public.

    Switching the booking engine on is what makes a property publicly bookable;
    switching it off takes it off sale immediately, because the public endpoints
    require the entitlement to resolve the property at all. Existing bookings
    are untouched -- this closes the shop door, it does not cancel anybody.
    """
    assert_property_in_org(db, caller, property_id)
    prop = _property_in_caller_org(db, caller, property_id)
    if module_code not in KNOWN_MODULES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown module '{module_code}'. Known modules: "
                   f"{', '.join(sorted(KNOWN_MODULES))}.")

    before = db.execute(
        text("SELECT enabled FROM iam.property_modules "
             "WHERE property_id = :p AND module_code = :m"),
        {"p": property_id, "m": module_code},
    ).scalar()

    # Permission is checked on the way ON, and never on the way off.
    #
    # Two gates decide whether a hotel is publicly bookable: the plan says
    # whether they may, this row says whether they are ready. Only the second
    # existed, so a property went on sale with no commercial answer behind it
    # at all.
    #
    # Switching OFF is deliberately never blocked. Whatever the billing state,
    # a hotel that wants to stop taking bookings must always be able to --
    # refusing that because an entitlement lapsed would trap somebody on sale.
    #
    # And a lapsed entitlement never disables anything by itself. It surfaces
    # on the property's readiness checks and somebody decides. A plan change
    # that quietly took a live hotel off sale, with guests mid-booking, is the
    # failure this ordering exists to prevent.
    if body.enabled and not before:
        entitled = db.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM billing.entitlements e
                    JOIN billing.subscriptions s ON s.id = e.subscription_id
                    WHERE s.organization_id = :o
                      AND s.status IN ('trialing', 'active', 'past_due',
                                       'grace')
                      AND e.kind = 'module'
                      AND e.code = :m
                )
                """
            ),
            {"o": prop["organization_id"], "m": module_code},
        ).scalar()
        if not entitled:
            raise HTTPException(
                status_code=403,
                detail=f"{KNOWN_MODULES[module_code]} is not included in this "
                       f"tenant's plan. Upgrade the subscription, or ask "
                       f"platform support to grant it.",
            )

    row = db.execute(
        text(
            """
            INSERT INTO iam.property_modules
                (property_id, module_code, enabled, enabled_at)
            VALUES (:p, :m, :on, CASE WHEN :on THEN now() END)
            ON CONFLICT (property_id, module_code) DO UPDATE
               SET enabled = EXCLUDED.enabled,
                   updated_at = now(),
                   -- Keep the original grant date across an off/on cycle only
                   -- when it is still on; a re-grant is a new date.
                   enabled_at = CASE
                       WHEN EXCLUDED.enabled
                        AND iam.property_modules.enabled THEN
                            iam.property_modules.enabled_at
                       WHEN EXCLUDED.enabled THEN now()
                       ELSE iam.property_modules.enabled_at END
            RETURNING enabled, enabled_at
            """
        ),
        {"p": property_id, "m": module_code, "on": body.enabled},
    ).mappings().first()

    # Putting a property on or off sale to the public is exactly the kind of
    # change somebody asks about a month later.
    record_audit(
        db,
        action="property_module_set",
        entity_type="property",
        entity_id=str(property_id),
        organization_id=prop["organization_id"],
        property_id=property_id,
        actor_subject=caller.subject,
        before={"module": module_code,
                "enabled": bool(before) if before is not None else None},
        after={"module": module_code, "enabled": body.enabled},
        reason=("Booking engine switched on." if body.enabled
                else "Booking engine switched off.")
        if module_code == "booking_engine" else None,
    )
    return schemas.ModuleOut(
        module_code=module_code, label=KNOWN_MODULES[module_code],
        enabled=row["enabled"], enabled_at=row["enabled_at"],
    )
