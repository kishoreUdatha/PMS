"""The platform principal: who may act above the tenant boundary, and how far.

Separate from ``authz.py`` on purpose, and the separation is the security
property rather than tidiness.

``authz.require_permission`` answers "does this caller hold a role granting
this action", and ``guard_request_tenancy`` answers "is the tenant they named
their own". Both are about a caller who lives *inside* one organisation. A
platform operator lives outside every one of them, so neither question applies
and neither dependency is used here.

The two must never meet:

* ``require_capability`` reads ``iam.platform_admins`` and the platform role
  tables, and nothing else. It does not consult tenant memberships, roles,
  role_assignments or permissions, so no tenant grant -- however broadly
  scoped -- can produce a platform caller.
* Nothing in this module calls ``guard_request_tenancy``. Platform routes are
  cross-tenant by definition; running the tenant guard on them would refuse
  every one.
* Conversely no tenant route may use these dependencies, or a platform admin
  would acquire tenant permissions they were never granted.

All of that is asserted by tests/test_platform_separation.py, which parses the
source, so the arrangement cannot be undone by someone adding a route in good
faith.

**Being platform staff is not the same as being allowed to do a thing.** A row
in ``platform_admins`` says only that somebody may reach this tier at all;
what they may then do comes from the roles assigned to it, each a set of named
capabilities. Every route names the capability it needs, so widening one is a
visible edit to that route rather than an invisible consequence of a role
change somewhere else.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from chirala_common.db import identity_context, system_context
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from .authz import Caller, get_caller
from .database import SessionFactory, get_session


@dataclass
class PlatformCaller:
    """An operator of the platform, belonging to no tenant.

    Deliberately carries no ``organization_id``. A platform administrator holds
    no membership in any organisation -- that is enforced when the grant is
    made -- so there is no "own" tenant for one of these callers to have, and a
    field holding one would be an invitation to treat it as a default.

    Every platform route names the organisation it acts on explicitly, in its
    path. Nothing here is ever scoped implicitly to the caller.
    """

    subject: str
    user_id: uuid.UUID
    admin_id: uuid.UUID
    #: Every capability this caller holds, across all of their roles. Carried
    #: so a handler can vary what it returns -- hiding an action the caller
    #: could not perform -- without a second round trip.
    capabilities: frozenset[str] = field(default_factory=frozenset)
    roles: tuple[str, ...] = ()

    def can(self, capability: str) -> bool:
        return capability in self.capabilities


def correlation_id(request: Request) -> str:
    """One id for everything a single request writes.

    The audit acceptance criteria ask for it, and the reason is legible the
    first time somebody reads a trail: suspending a tenant writes an
    organisation row and revokes sessions, and without a shared id those are
    two unrelated entries that happen to share a timestamp.

    Taken from the caller's own header when they sent one, so a correlation id
    already travelling with the request survives into the audit log rather
    than being replaced by a fresh one.
    """
    existing = getattr(request.state, "correlation_id", None)
    if existing:
        return existing
    header = (request.headers.get("x-correlation-id") or "").strip()
    value = header[:100] if header else uuid.uuid4().hex
    request.state.correlation_id = value
    return value


def _record_denial(*, subject: str, user_id: uuid.UUID | None,
                   capability: str, path: str, method: str,
                   correlation: str, reason: str) -> None:
    """Write a refusal to the audit log, on its own connection.

    This has to outlive the rejection. The route is transactional and rolls
    back on exception, so a denial recorded on the request's session is undone
    by the very 403 that caused it -- the refusals would never appear, which
    is exactly backwards: a staff member probing what they cannot reach is the
    thing most worth seeing.

    Failures here are swallowed. An audit write that cannot happen must not
    turn a clean 403 into a 500, because that would tell the caller something
    about the system that the refusal was meant to withhold.
    """
    try:
        with SessionFactory() as session:
            system_context(session, reason="platform: record a denied action")
            session.execute(
                text(
                    """
                    INSERT INTO iam.audit_events
                        (id, actor_subject, action, entity_type, entity_id,
                         reason, correlation_id, redacted_after, occurred_at)
                    VALUES (gen_random_uuid(), :actor, 'platform.access.denied',
                            'platform_capability', :cap, :reason, :corr,
                            -- jsonb_build_object takes `any`, so an
                            -- uncast parameter leaves Postgres with nothing
                            -- to infer a type from and it refuses the whole
                            -- statement.
                            jsonb_build_object(
                                'method', CAST(:method AS text),
                                'path', CAST(:path AS text),
                                'user_id', CAST(:uid AS text),
                                'result', 'denied'),
                            now())
                    """
                ),
                {"actor": subject, "cap": capability, "reason": reason,
                 "corr": correlation, "method": method, "path": path,
                 "uid": str(user_id) if user_id else None},
            )
            session.commit()
    except Exception:  # noqa: BLE001 - see docstring
        # Swallowed, but never silently: an audit trail that has started
        # losing entries is itself the incident, and a refusal that vanishes
        # is indistinguishable from one that never happened.
        logging.getLogger(__name__).exception(
            "could not record denied platform action: %s on %s %s",
            capability, method, path)


def _load(db: Session, user_id: uuid.UUID) -> tuple[uuid.UUID, frozenset[str], tuple[str, ...]] | None:
    """This caller's platform row and everything their roles grant.

    Runs under identity context, which the RLS policies are written to allow:
    ``platform_admins`` admits a caller to their own row, the role assignments
    admit the ones belonging to it, and the catalogue tables are readable by
    anyone because they are not tenant data.
    """
    row = db.execute(
        text(
            """
            SELECT pa.id,
                   coalesce(array_agg(DISTINCT pp.code)
                            FILTER (WHERE pp.code IS NOT NULL), '{}') AS caps,
                   coalesce(array_agg(DISTINCT pr.code)
                            FILTER (WHERE pr.code IS NOT NULL), '{}') AS roles
            FROM iam.platform_admins pa
            LEFT JOIN iam.platform_role_assignments ra
                   ON ra.platform_admin_id = pa.id
            LEFT JOIN iam.platform_roles pr ON pr.id = ra.role_id
            LEFT JOIN iam.platform_role_permissions rp ON rp.role_id = pr.id
            LEFT JOIN iam.platform_permissions pp ON pp.id = rp.permission_id
            WHERE pa.user_id = :uid AND pa.status = 'active'
            GROUP BY pa.id
            LIMIT 1
            """
        ),
        {"uid": user_id},
    ).first()
    if row is None:
        return None
    return row.id, frozenset(row.caps or ()), tuple(row.roles or ())


def require_capability(capability: str):
    """Dependency factory: the caller holds this capability, or 403.

    Deny by default twice over. A user with no platform row is refused
    whatever else they hold -- including an organisation-wide Administrator
    role, the strongest thing a tenant can grant, which confers nothing here.
    A user with a platform row but without this capability is refused too,
    which is the whole point of the tier having roles at all.
    """

    def _dep(request: Request,
             caller: Caller = Depends(get_caller),
             db: Session = Depends(get_session)) -> PlatformCaller:
        corr = correlation_id(request)
        path, method = request.url.path, request.method

        if caller.user_id is None:
            # get_caller already rejected the unauthenticated; this is a token
            # for a subject with no user row.
            _record_denial(subject=caller.subject, user_id=None,
                           capability=capability, path=path, method=method,
                           correlation=corr, reason="no user record")
            raise HTTPException(status_code=403, detail="Not platform staff")

        # Verify first, elevate second, and in that order deliberately.
        #
        # get_caller ends by binding the caller's tenant, which clears
        # app.identity_subject -- so at this point the transaction can see
        # neither every tenant nor this caller's own platform row. Something
        # has to widen it to ask the question. Reaching for system context is
        # the obvious move and the wrong one: it would mean every caller who
        # merely *attempts* a platform route spends the next statement able to
        # read every tenant, and only the 403 below keeps that window shut.
        # Nothing reads anything in that window today, which is exactly the
        # kind of safety that lasts until somebody adds a line.
        #
        # The RLS policies are written to make it unnecessary: platform_admins
        # admits a caller to their own row, and the role tables to their own
        # grants. Measured, not assumed -- under identity context this query
        # returns the caller's capabilities while iam.organizations returns
        # zero rows.
        identity_context(db, subject=caller.subject)
        found = _load(db, caller.user_id)

        if found is None:
            # Refused while the transaction can still see only this one row.
            _record_denial(subject=caller.subject, user_id=caller.user_id,
                           capability=capability, path=path, method=method,
                           correlation=corr, reason="not platform staff")
            raise HTTPException(status_code=403, detail="Not platform staff")

        admin_id, caps, roles = found
        if capability not in caps:
            _record_denial(
                subject=caller.subject, user_id=caller.user_id,
                capability=capability, path=path, method=method,
                correlation=corr,
                reason=f"holds {sorted(roles) or 'no roles'}, lacks {capability}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your platform role does not include {capability}.")

        # Established, and only now. Every platform route behind this
        # dependency runs in named system context from here.
        system_context(
            db, reason=f"platform: {capability} for {caller.subject}")
        return PlatformCaller(
            subject=caller.subject,
            user_id=caller.user_id,
            admin_id=admin_id,
            capabilities=caps,
            roles=roles,
        )

    return _dep
