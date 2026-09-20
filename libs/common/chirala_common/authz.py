"""Shared authorization: caller principal + deny-by-default permission checks (§2).

Authorization = active membership AND scoped role permission AND record
relationship (property scope). Every service that guards a route uses this
module so the rule is defined once; the permission data itself lives in the
``iam`` schema on the shared cluster, which every service can read.

Identity source: a validated OIDC token (subject) in production. For local
development before Keycloak is fully wired, the caller subject may be supplied
via the ``X-Debug-Subject`` header; this is accepted ONLY when ENVIRONMENT=local.
Real deployments must reject it (the gateway/token validator provides identity).

Services wire this up once at import time::

    caller_dep, require_permission, require_org_permission = build_authz(
        get_session, lambda: settings.environment
    )
"""

from __future__ import annotations

from .db import bind_tenant_context, identity_context

import base64
import hmac
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from fastapi import Request, Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class Caller:
    """The resolved, authenticated caller."""

    subject: str
    user_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    #: True when this is the platform calling itself rather than a person.
    #:
    #: A webhook has no user. Neither does a scheduled job, or one service
    #: completing work another started. Those calls still need permission to
    #: act, and there is no membership to grant it — so the platform presents
    #: its own credential and is trusted to be acting on its own behalf.
    #:
    #: It is *not* a bypass of tenancy. A service caller still names the
    #: organisation it is acting for, and ``assert_property_in_org`` still
    #: refuses a property outside it. A bug in an internal call should be
    #: unable to reach another customer's data, whatever it was told to do.
    is_service: bool = False

    #: True when the caller's organisation has been suspended by the platform.
    #:
    #: Resolved here rather than checked by each route, because suspension has
    #: to bite on the next request in every service -- not at the next sign-in.
    #: A tenant whose staff keep working on tokens issued before the
    #: suspension has not been suspended in any sense that matters.
    org_suspended: bool = False

    #: Tenant ids already verified during this request. The Caller is built
    #: once per request, so this is per-request state: the dependency vets a
    #: tenant on the way in, and a handler that vets the same one again --
    #: belt and braces, or older code -- costs nothing instead of a second
    #: round trip.
    verified: set = field(default_factory=set)

    def already_verified(self, kind: str, value) -> bool:
        """True if ``value`` has been checked for this request already."""
        return (kind, str(value)) in self.verified

    def mark_verified(self, kind: str, value) -> None:
        self.verified.add((kind, str(value)))


def _refuse_if_suspended(caller: Caller) -> None:
    """403 when the caller's organisation has been suspended by the platform.

    A distinct message from "permission denied" on purpose. The staff of a
    suspended tenant have done nothing wrong and their roles are intact; being
    told they lack a permission they can see themselves holding sends them to
    their own administrator, who cannot fix it either.
    """
    if caller.org_suspended:
        raise HTTPException(
            status_code=403,
            detail="This organisation is suspended. Contact support.",
        )


def subject_from_bearer(authorization: str | None) -> str | None:
    """Extract the subject from a dev session token (base64 ``dev:<subject>``).

    Replace with JWT ``sub`` validation (``chirala_common.auth``) when Keycloak
    is wired; this is the single seam that has to change.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        raw = base64.urlsafe_b64decode(authorization.split(" ", 1)[1].encode()).decode()
    except Exception:  # noqa: BLE001
        return None
    return raw[4:] if raw.startswith("dev:") else None


# Query shared by both permission dependencies. ``:prop IS NULL`` makes the
# property predicate collapse to organization-scope-only, which is what
# org-level administration screens need.
_GRANT_SQL = """
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
            -- Cast explicitly: an org-scoped check passes NULL here, and a bare
            -- parameter used both in `IS NULL` and in a comparison gives
            -- Postgres nothing to infer a type from.
            CAST(:prop AS uuid) IS NULL
         OR ra.scope_type = 'organization'
         OR ra.property_id = CAST(:prop AS uuid)
      )
    LIMIT 1
"""


#: Path/query parameter name -> the table whose row it names. Read off the
#: request rather than declared as dependency parameters: declaring them would
#: add ``?folio_id=`` and friends to the OpenAPI schema of every endpoint in
#: the deployment, whereas reading them costs nothing and misses nothing.
_ENTITY_PARAMS = {
    "reservation_id": "booking.reservations",
    "unit_id": "booking.reservation_units",
    "guest_id": "engagement.guests",
    "folio_id": "finance.folios",
}


def _uuid_or_none(raw):
    """Parse a tenant id, or give up quietly.

    A malformed id is not this function's problem -- FastAPI's own validation
    rejects it with a 422 a moment later, and guessing here would turn a bad
    request into a confusing 403.
    """
    if raw in (None, ""):
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def guard_request_tenancy(request: Request, db: Session, caller: Caller) -> None:
    """Vet every tenant and tenant-owned id this request carries.

    Called from the permission dependencies, so it runs for every route that
    asks for a permission -- which is every authenticated route -- before the
    handler body starts. That is the whole point: the guards used to be lines
    a route author had to remember, and across three services 52 handlers did
    not. Now forgetting is not an available mistake; a route is guarded by
    existing.

    Both the path and the query string are inspected. ``/folios/{folio_id}``
    puts the id in the path and ``/folios?reservation_id=`` puts it in the
    query, and both were leaking before this.
    """
    sources = [dict(request.path_params), dict(request.query_params)]

    for src in sources:
        org = _uuid_or_none(src.get("organization_id"))
        if org is not None:
            assert_org_matches_caller(caller, org)

    for src in sources:
        prop = _uuid_or_none(src.get("property_id"))
        if prop is not None:
            assert_property_in_org(db, caller, prop)

    for src in sources:
        for name, table in _ENTITY_PARAMS.items():
            eid = _uuid_or_none(src.get(name))
            if eid is not None:
                assert_entity_in_org(db, caller, table=table, entity_id=eid)


def build_authz(
    get_session: Callable[[], Iterator[Session]],
    environment: Callable[[], str],
    service_token: Callable[[], str] = lambda: "",
):
    """Build the authz dependencies bound to one service's session factory.

    Returns ``(get_caller, require_permission, require_org_permission)``.

    ``service_token`` is the shared secret this deployment uses for calls
    between its own services. Unset, service authentication is simply off and
    every caller must be a person — which is the right default, because a
    deployment that forgot to configure a secret must not end up accepting an
    empty one.
    """

    def get_caller(
        x_debug_subject: str | None = Header(default=None),
        x_service_token: str | None = Header(default=None),
        x_service_org: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        db: Session = Depends(get_session),
    ) -> Caller:
        secret = service_token()
        if secret and x_service_token:
            # compare_digest, not ==. A plain comparison stops at the first
            # differing byte, and how long it took says how much of a guess
            # was right.
            if hmac.compare_digest(x_service_token, secret):
                org: uuid.UUID | None = None
                if x_service_org:
                    try:
                        org = uuid.UUID(x_service_org)
                    except ValueError:
                        raise HTTPException(
                            status_code=400,
                            detail="X-Service-Org is not a valid id.",
                        ) from None
                bind_tenant_context(db, organization_id=org, is_service=True)
                return Caller(subject="service", user_id=None,
                              organization_id=org, is_service=True)
            # A wrong token is not a fall-through to other credentials. It is
            # somebody guessing, and it stops here.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid service credential",
            )

        subject = subject_from_bearer(authorization)
        if subject is None and environment() == "local":
            subject = x_debug_subject
        if not subject:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthenticated: no valid credentials",
            )

        # Who is calling is read before any tenant is known, so the lookup is
        # allowed this subject's own identity rows and nothing more.
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
        # And immediately narrowed to the caller's own tenant, so no handler
        # ever runs in identity context.
        bind_tenant_context(db, organization_id=row.organization_id if row else None,
                            user_id=row.user_id if row else None)
        return Caller(
            subject=subject,
            user_id=row.user_id if row else None,
            organization_id=row.organization_id if row else None,
            org_suspended=org_status is not None and org_status != "active",
        )

    def _check(
        db: Session,
        caller: Caller,
        resource_code: str,
        action_code: str,
        property_id: uuid.UUID | None,
    ) -> Caller:
        if caller.is_service:
            # The platform acting on its own behalf. There is no membership to
            # check because there is no person; the credential itself is the
            # authorisation. Tenancy is still enforced separately, by
            # assert_property_in_org on the routes that touch a property.
            return caller
        if caller.user_id is None or caller.organization_id is None:
            raise HTTPException(status_code=403, detail="No active membership")
        granted = db.execute(
            text(_GRANT_SQL),
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

    def require_permission(resource_code: str, action_code: str):
        """Enforce a permission for the target property (from ``property_id``)."""

        def _dep(
            request: Request,
            property_id: uuid.UUID | None = None,
            caller: Caller = Depends(get_caller),
            db: Session = Depends(get_session),
        ) -> Caller:
            # Suspension outranks both. A suspended tenant's staff still
            # hold every role they held yesterday, so the permission check
            # would pass and say nothing about why the account is stopped.
            _refuse_if_suspended(caller)
            # The caller's organisation comes from their credential, not from
            # anything they sent, so it is bound before the guard -- whose own
            # lookups read tables that row-level security filters. The
            # property is client-supplied and waits until the guard vets it.
            bind_tenant_context(db, organization_id=caller.organization_id,
                                user_id=caller.user_id,
                                is_service=caller.is_service)
            # Permission first, tenancy second. Both must pass; the order only
            # decides which refusal a caller sees, and "you may not do this at
            # all" is the more useful one.
            _check(db, caller, resource_code, action_code, property_id)
            guard_request_tenancy(request, db, caller)
            # Only now, with the tenant vetted, does the transaction learn
            # whose data it may touch.
            bind_tenant_context(db, organization_id=caller.organization_id,
                                property_id=property_id, user_id=caller.user_id,
                                is_service=caller.is_service)
            return caller

        return _dep

    def require_org_permission(resource_code: str, action_code: str):
        """Enforce a permission at ORGANIZATION scope (no target property)."""

        def _dep(
            request: Request,
            caller: Caller = Depends(get_caller),
            db: Session = Depends(get_session),
        ) -> Caller:
            _refuse_if_suspended(caller)
            # The caller's organisation comes from their credential, not from
            # anything they sent, so it is bound before the guard -- whose own
            # lookups read tables that row-level security filters. The
            # property is client-supplied and waits until the guard vets it.
            bind_tenant_context(db, organization_id=caller.organization_id,
                                user_id=caller.user_id,
                                is_service=caller.is_service)
            _check(db, caller, resource_code, action_code, None)
            guard_request_tenancy(request, db, caller)
            bind_tenant_context(db, organization_id=caller.organization_id,
                                user_id=caller.user_id,
                                is_service=caller.is_service)
            return caller

        return _dep

    return get_caller, require_permission, require_org_permission


def caller_org(caller: Caller) -> uuid.UUID:
    """The organisation to write into a new row, taken from the caller.

    The counterpart to the guards: they stop a caller naming somebody else's
    tenant, and this removes the need to name one at all. A create body used
    to carry ``organization_id`` and the handler inserted it verbatim, so the
    row landed in whichever tenant the request asked for. Now the field does
    not exist on the model and there is nothing to get wrong.

    Refuses rather than returning ``None``: a caller with no organisation --
    a service credential sent without ``X-Service-Org``, or a user whose
    membership has been revoked -- must not write a tenantless row that every
    later tenancy check then fails to match.
    """
    if caller.organization_id is None:
        raise HTTPException(
            status_code=403, detail="Caller does not belong to an organisation")
    return caller.organization_id


def assert_org_matches_caller(
    caller: Caller, organization_id: uuid.UUID | None
) -> None:
    """Reject a request that names an organisation other than the caller's own.

    The org-scoped twin of :func:`assert_property_in_org`, and it exists for
    the same reason. Where a route takes ``property_id``, that function turns
    the id into a tenant and refuses one outside the caller's. Where a route
    takes ``organization_id`` instead, there was nothing equivalent: the
    permission check confirmed the caller may read guests *somewhere*, and the
    query then read guests from whichever organisation the URL asked for.

    A permission is an answer to "may this caller do this?", never to "whose
    data is this?". Only this comparison answers the second question, so a
    handler that accepts an organisation from the caller must call it.

    No database round trip: the caller's organisation is already resolved, and
    the parameter is only ever allowed to equal it.
    """
    if organization_id is None:
        return
    if caller.already_verified("org", organization_id):
        return
    if caller.organization_id is None or organization_id != caller.organization_id:
        # Deliberately the same wording and status as the property-scoped
        # refusal: a prober learns that the answer is "no", not whether the
        # organisation they guessed happens to exist.
        raise HTTPException(
            status_code=403, detail="Organization outside caller tenant"
        )
    caller.mark_verified("org", organization_id)


#: Tables an entity guard may be pointed at. A literal allowlist because the
#: table name is interpolated into SQL: every caller passes a constant today,
#: and this makes it impossible for one to stop doing so quietly.
_TENANT_TABLES = {
    "booking.reservations",
    "booking.reservation_units",
    "engagement.guests",
    "finance.folios",
}


def assert_entity_in_org(
    db: Session, caller: Caller, *, table: str, entity_id, id_column: str = "id"
) -> None:
    """Reject a row addressed by bare id that belongs to another tenant.

    The third guard, for the case the other two cannot see. ``/folios/{id}``
    and ``/reservations/{id}/full`` name no tenant at all -- there is no
    ``property_id`` to vet and no ``organization_id`` to compare, only a uuid
    -- so both other guards have nothing to check and the row came back to
    whoever asked. A uuid is not a secret: it appears in URLs, exports,
    confirmation emails and webhook payloads.

    **404, not 403.** The other guards answer about a tenant the caller named,
    so refusing tells them nothing they did not already supply. Here the id is
    the whole request, and a 403 would confirm the row exists -- turning the
    endpoint into an oracle for guessing at other tenants' volume. Absent and
    not-yours are deliberately the same answer.
    """
    if table not in _TENANT_TABLES:
        raise ValueError(f"assert_entity_in_org: unknown table {table!r}")
    if caller.already_verified(table, entity_id):
        return
    if caller.organization_id is None:
        raise HTTPException(status_code=404, detail="Not found")
    row = db.execute(
        text(
            f"""
            SELECT 1 FROM {table}
            WHERE {id_column} = :eid AND organization_id = :org
            LIMIT 1
            """  # noqa: S608 - table and column are allowlisted literals
        ),
        {"eid": entity_id, "org": caller.organization_id},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    caller.mark_verified(table, entity_id)


def assert_property_in_org(
    db: Session, caller: Caller, property_id: uuid.UUID
) -> None:
    """Reject cross-tenant access: the property must belong to the caller's org.

    Permission checks alone are not enough — a caller with an organization-scoped
    role must still be stopped from naming another tenant's property.
    """
    row = db.execute(
        text(
            """
            SELECT 1 FROM iam.properties
            WHERE id = :prop AND organization_id = :org
            LIMIT 1
            """
        ),
        {"prop": property_id, "org": caller.organization_id},
    ).first()
    if row is None:
        raise HTTPException(status_code=403, detail="Property outside caller tenant")
    caller.mark_verified("prop", property_id)
