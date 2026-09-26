"""Authorization: caller principal + deny-by-default permission checks (§2).

Authorization = active membership AND scoped role permission AND record
relationship (property scope). This module resolves the caller and enforces a
required (resource, action) permission for the target property.

Identity source: the signed session token iam itself issues, checked by
``chirala_common.session_tokens`` exactly as booking-core and finance check it.
For local development the caller subject may also be supplied via the
``X-Debug-Subject`` header; this is accepted ONLY when ENVIRONMENT is exactly
``local``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from chirala_common.session_tokens import subject_from_bearer

from .database import get_session
from .settings import settings


@dataclass
class Caller:
    subject: str
    user_id: uuid.UUID | None
    organization_id: uuid.UUID | None

    #: This service keeps its own Caller, so it must carry the same
    #: per-request memo the shared guards in chirala_common.authz write to.
    #: Without it those guards raise AttributeError the first time iam calls
    #: one -- which it now does on every permission check.
    is_service: bool = False
    #: Mirrors chirala_common.authz.Caller. Suspension is resolved with the
    #: caller so it bites on the next request rather than the next sign-in.
    org_suspended: bool = False
    verified: set = field(default_factory=set)

    def already_verified(self, kind: str, value) -> bool:
        return (kind, str(value)) in self.verified

    def mark_verified(self, kind: str, value) -> None:
        self.verified.add((kind, str(value)))


def get_caller(
    x_debug_subject: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
) -> Caller:
    """Resolve the authenticated caller.

    From the signed session token, through the same check every other service
    uses. Local dev only: fall back to X-Debug-Subject so the flow is testable
    without signing in.
    """
    subject = subject_from_bearer(authorization, db)
    if subject is None and settings.environment == "local":
        subject = x_debug_subject

    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthenticated: no valid credentials",
        )

    # Read before any tenant is known: this subject's own identity rows only.
    identity_context(db, subject=subject)
    row = db.execute(
        text(
            """
            SELECT u.id AS user_id, m.organization_id,
                   o.status AS org_status
            FROM iam.users u
            LEFT JOIN iam.memberships m
              ON m.user_id = u.id AND m.status = 'active'
            LEFT JOIN iam.organizations o
              ON o.id = m.organization_id
            WHERE u.subject_id = :sub AND u.status = 'active'
            LIMIT 1
            """
        ),
        {"sub": subject},
    ).first()
    org_status = row.org_status if row else None
    # Narrowed to the caller's own tenant before any handler runs.
    bind_tenant_context(db, organization_id=row.organization_id if row else None,
                        user_id=row.user_id if row else None)
    return Caller(
        subject=subject,
        user_id=row.user_id if row else None,
        organization_id=row.organization_id if row else None,
        org_suspended=org_status is not None and org_status != "active",
    )


# Tenancy is a separate question from permission, and this service had no
# answer to it. ``require_permission`` below asks whether the caller holds a
# role granting the action; an organisation-scoped assignment satisfies that
# for *any* property_id, because the grant is not tied to one. Something still
# has to ask whether the named property is the caller's, and that is this.
#
# Re-exported from chirala_common rather than reimplemented: one definition of
# what "outside the caller's tenant" means, shared with booking-core and
# finance. It reads only ``caller.organization_id``, which this service's own
# Caller also carries.
from chirala_common.db import bind_tenant_context, identity_context
from chirala_common.authz import (  # noqa: F401
    _GRANT_SQL,
    _uuid_or_none,
    assert_property_in_org,
    guard_request_tenancy,
    require_property_permission,
)


def require_permission(resource_code: str, action_code: str):
    """Dependency factory: enforce a scoped permission for the target property.

    Deny by default. The caller must have an active membership and, through some
    active role assignment scoped to the property, a role_permission matching
    (resource_code, action_code).
    """

    def _dep(
        request: Request,
        property_id: uuid.UUID | None = None,
        caller: Caller = Depends(get_caller),
        db: Session = Depends(get_session),
    ) -> Caller:
        if caller.user_id is None or caller.organization_id is None:
            raise HTTPException(status_code=403, detail="No active membership")
        if caller.org_suspended:
            # Not "permission denied": their roles are intact and they can see
            # that, so naming the real reason is what sends them somewhere
            # that can actually help.
            raise HTTPException(
                status_code=403,
                detail="This organisation is suspended. Contact support.")
        # Tenancy, before the permission query and before the handler. This
        # service needed it most: the grant below matches when the assignment
        # is organisation-scoped, `ra.scope_type = 'organization'`, whatever
        # property was named -- so the permission check alone would have said
        # yes to another tenant's property the moment anyone held an org-wide
        # role.
        bind_tenant_context(db, organization_id=caller.organization_id,
                            user_id=caller.user_id)
        guard_request_tenancy(request, db, caller)
        bind_tenant_context(db, organization_id=caller.organization_id,
                            property_id=property_id, user_id=caller.user_id)

        granted = db.execute(
            text(
                """
                SELECT 1
                FROM iam.memberships m
                JOIN iam.role_assignments ra ON ra.membership_id = m.id
                JOIN iam.role_permissions rp ON rp.role_id = ra.role_id
                JOIN iam.permissions p ON p.id = rp.permission_id
                WHERE m.user_id = :uid
                  AND m.status = 'active'
                  AND p.resource_code = :res
                  AND p.action_code = :act
                  AND (
                        ra.scope_type = 'organization'
                     OR (:prop IS NOT NULL AND ra.property_id = :prop)
                  )
                LIMIT 1
                """
            ),
            {
                "uid": caller.user_id,
                "res": resource_code,
                "act": action_code,
                "prop": property_id,
            },
        ).first()
        if granted is None:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {resource_code}.{action_code}",
            )
        return caller

    return _dep


def require_org_permission(resource_code: str, action_code: str):
    """Dependency factory: enforce a permission at ORGANIZATION scope.

    For org-level administration (e.g. User Management) where there is no target
    property. The caller must have the permission via ANY active role assignment
    in their organization. Deny by default.
    """

    def _dep(
        request: Request,
        caller: Caller = Depends(get_caller),
        db: Session = Depends(get_session),
    ) -> Caller:
        if caller.user_id is None or caller.organization_id is None:
            raise HTTPException(status_code=403, detail="No active membership")
        if caller.org_suspended:
            # Not "permission denied": their roles are intact and they can see
            # that, so naming the real reason is what sends them somewhere
            # that can actually help.
            raise HTTPException(
                status_code=403,
                detail="This organisation is suspended. Contact support.")
        bind_tenant_context(db, organization_id=caller.organization_id,
                            user_id=caller.user_id)
        guard_request_tenancy(request, db, caller)
        bind_tenant_context(db, organization_id=caller.organization_id,
                            user_id=caller.user_id)

        granted = db.execute(
            text(
                """
                SELECT 1
                FROM iam.memberships m
                JOIN iam.role_assignments ra ON ra.membership_id = m.id
                JOIN iam.role_permissions rp ON rp.role_id = ra.role_id
                JOIN iam.permissions p ON p.id = rp.permission_id
                WHERE m.user_id = :uid
                  AND m.status = 'active'
                  AND p.resource_code = :res
                  AND p.action_code = :act
                LIMIT 1
                """
            ),
            {"uid": caller.user_id, "res": resource_code, "act": action_code},
        ).first()
        if granted is None:
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {resource_code}.{action_code}",
            )
        # A route that names a property in its path or query acts on that
        # property, so the grant has to reach it: an assignment on another
        # hotel in the same organisation is not permission here. The same
        # rule as the shared require_org_permission.
        for src in (request.path_params, request.query_params):
            prop = _uuid_or_none(src.get("property_id"))
            if prop is not None:
                require_property_permission(db, caller, prop,
                                            resource_code, action_code)
        return caller

    return _dep
