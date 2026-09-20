"""Platform administration: the console above the tenant boundary (US-132).

Every route here is cross-tenant by design, which is exactly what the rest of
the system spends its effort preventing. Three things keep that honest:

* the only gate is ``require_capability``, which reads
  ``iam.platform_admins`` and the platform role tables and consults no
  tenant membership, role or permission -- so nothing a tenant can grant
  reaches these routes, and every route below names the one capability it
  needs rather than accepting anyone who holds platform access at all;
* ``guard_request_tenancy`` is deliberately absent, because these routes name
  somebody else's tenant on purpose. It stays mandatory everywhere else;
* every write is audited to ``iam.audit_events`` against the organisation it
  touched, so a tenant's own audit log shows what the platform did to them.

What is deliberately NOT here is as much the design as what is: no route reads
guest data, folio detail or payment credentials, and none posts money. A
platform operator runs the platform; they do not transact inside a customer's
books. Support questions that genuinely need a tenant's data are answered by
someone in that tenant, or by a break-glass path that does not exist yet and
should be built as one when it is needed -- consented, time-boxed and loud.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from smtplib import SMTPException

from chirala_common.db import system_context
from chirala_common.objectstore import (
    ObjectStoreConfig, ObjectStoreError, get_archive, put_archive,
)
from chirala_common.delivery_log import record_delivery
from chirala_common.mailer import MailNotConfigured, send as send_mail
from chirala_common.routing import TransactionalRoute
from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .audit import record_audit
from .auth_routes import (
    _normalise_email,
    _new_property_code,
    _seed_property_defaults,
    _seed_roles,
    issue_password_token,
)
from .database import SessionFactory, get_session
from .mail import owner_invitation_email
from .platform_authz import (
    PlatformCaller,
    correlation_id,
    require_capability,
)
from .settings import settings
from .tenant_deletion import export_tenant, purge, survey

#: The same bucket the rest of the service writes to. Spelled out here rather
#: than imported from onboarding_routes so this module does not depend on that
#: one for a value object -- but it must stay identical to it.
_STORE = ObjectStoreConfig(
    endpoint=settings.minio_endpoint,
    public_endpoint=settings.minio_public_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    bucket=settings.minio_bucket,
    secure=settings.minio_secure,
    url_ttl_seconds=settings.minio_url_ttl_seconds,
)

platform_router = APIRouter(
    prefix="/platform",
    tags=["platform"],
    route_class=TransactionalRoute,
)

WELCOME_HOURS = 72

#: The three services that own migrations, and the schemas each one creates.
#:
#: Only these three keep an ``alembic_version``. Listing the others alongside
#: them made the overview report "4 not migrated" for schemas that are simply
#: not independently versioned -- an alarm for a healthy deployment, which is
#: worse than no alarm at all.
MIGRATION_OWNERS = {
    "iam": ("iam", "billing"),
    "booking": ("booking", "property", "engagement", "operations",
                "distribution"),
    "finance": ("finance",),
}


def _rows(db: Session, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params or {}).mappings()]


def _one(db: Session, sql: str, params: dict) -> dict | None:
    r = db.execute(text(sql), params).mappings().first()
    return dict(r) if r else None


def _org_or_404(db: Session, org_id: uuid.UUID) -> dict:
    org = _one(
        db,
        "SELECT id, code, name, status FROM iam.organizations WHERE id = :o",
        {"o": org_id},
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org


# ============================================================= tenants =====

@platform_router.get("/organizations")
def list_organizations(
    query: str | None = None,
    status: str | None = None,
    plan: str | None = None,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.view")),
):
    """Every tenant on the platform, with enough to triage one.

    Counts rather than contents: how many properties and people an
    organisation has is operational, what their guests are called is not.
    """
    return _rows(
        db,
        """
        SELECT o.id, o.code, o.name, o.status,
               to_char(o.created_at, 'DD Mon YYYY') AS created_on,
               (SELECT count(*) FROM iam.properties p
                 WHERE p.organization_id = o.id) AS properties,
               (SELECT count(*) FROM property.rooms r
                 WHERE r.organization_id = o.id) AS rooms,
               (SELECT count(*) FROM iam.memberships m
                 WHERE m.organization_id = o.id
                   AND m.status = 'active') AS active_users,
               (SELECT max(a.occurred_at) FROM iam.audit_events a
                 WHERE a.organization_id = o.id) AS last_activity_at,
               pl.code AS plan_code, pl.name AS plan_name,
               sub.status AS subscription_status,
               COALESCE(sub.amount * sub.quantity, 0) AS mrr,
               -- What the directory calls a tenant's lifecycle is the
               -- subscription's state when there is one and the account's
               -- when there is not: an organisation nobody has put on a plan
               -- is not "active" in any sense a commercial screen means.
               COALESCE(sub.status, o.status) AS lifecycle
        FROM iam.organizations o
        LEFT JOIN billing.subscriptions sub
               ON sub.organization_id = o.id
              AND sub.status IN ('trialing', 'active', 'past_due', 'grace')
        LEFT JOIN billing.plan_versions pv ON pv.id = sub.plan_version_id
        LEFT JOIN billing.plans pl ON pl.id = pv.plan_id
        WHERE (CAST(:q AS text) IS NULL
               OR o.name ILIKE '%' || :q || '%'
               OR upper(o.code) = upper(:q))
          AND (CAST(:st AS text) IS NULL
               OR COALESCE(sub.status, o.status) = :st)
          AND (CAST(:plan AS text) IS NULL OR pl.code = :plan)
        ORDER BY o.code
        """,
        {"q": query or None, "st": status or None, "plan": plan or None},
    )


@platform_router.get("/organizations/{org_id}")
def get_organization(
    org_id: uuid.UUID,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.view")),
):
    org = _org_or_404(db, org_id)
    org["properties"] = _rows(
        db,
        """
        SELECT id, code, name, status, timezone, currency, contact_email
        FROM iam.properties WHERE organization_id = :o ORDER BY created_at
        """,
        {"o": org_id},
    )
    org["users"] = _rows(
        db,
        """
        SELECT u.id, u.display_name, u.email, u.status AS user_status,
               m.status AS membership_status, u.last_login_at
        FROM iam.memberships m
        JOIN iam.users u ON u.id = m.user_id
        WHERE m.organization_id = :o
        ORDER BY u.display_name
        """,
        {"o": org_id},
    )
    return org


class OrgRename(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    reason: str | None = Field(default=None, max_length=300)


@platform_router.patch("/organizations/{org_id}")
def rename_organization(
    request: Request,
    org_id: uuid.UUID,
    body: OrgRename,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.lifecycle")),
):
    before = _org_or_404(db, org_id)
    name = body.name.strip()
    db.execute(
        text("UPDATE iam.organizations SET name = :n, updated_at = now(), "
             "version = version + 1 WHERE id = :o"),
        {"n": name, "o": org_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request), action="platform.organization.renamed", entity_type="organization",
        entity_id=str(org_id), organization_id=org_id,
        actor_subject=admin.subject, reason=body.reason,
        before={"name": before["name"]}, after={"name": name},
    )
    return {"id": str(org_id), "name": name}


class OrgSuspend(BaseModel):
    # Required, not optional. Cutting a paying customer off is not something
    # that should be possible without saying why, and the tenant's own audit
    # log is where they will look for the answer.
    reason: str = Field(min_length=3, max_length=300)


@platform_router.post("/organizations/{org_id}/suspend")
def suspend_organization(
    request: Request,
    org_id: uuid.UUID,
    body: OrgSuspend,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.lifecycle")),
):
    """Stop a tenant using the platform, without touching their data.

    Suspension is enforced where the caller is resolved, so it takes effect on
    the next request in every service rather than only at the next sign-in --
    a user already holding a session is stopped too.
    """
    org = _org_or_404(db, org_id)
    if org["status"] == "suspended":
        raise HTTPException(status_code=409, detail="Already suspended")
    db.execute(
        text("UPDATE iam.organizations SET status = 'suspended', "
             "updated_at = now(), version = version + 1 WHERE id = :o"),
        {"o": org_id},
    )
    # Sessions are revoked as well as access refused. Leaving them alive would
    # mean a suspended tenant's staff kept a token that every service has to
    # keep re-rejecting, and any gap in that enforcement is a way back in.
    revoked = db.execute(
        text("UPDATE iam.login_sessions SET revoked_at = now() "
             "WHERE organization_id = :o AND revoked_at IS NULL"),
        {"o": org_id},
    ).rowcount
    record_audit(
        db, correlation_id=correlation_id(request), action="platform.organization.suspended",
        entity_type="organization", entity_id=str(org_id),
        organization_id=org_id, actor_subject=admin.subject,
        reason=body.reason,
        before={"status": org["status"]},
        after={"status": "suspended", "sessions_revoked": revoked},
    )
    return {"id": str(org_id), "status": "suspended",
            "sessions_revoked": revoked}


@platform_router.post("/organizations/{org_id}/reactivate")
def reactivate_organization(
    request: Request,
    org_id: uuid.UUID,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.lifecycle")),
):
    org = _org_or_404(db, org_id)
    if org["status"] == "active":
        raise HTTPException(status_code=409, detail="Already active")
    db.execute(
        text("UPDATE iam.organizations SET status = 'active', "
             "updated_at = now(), version = version + 1 WHERE id = :o"),
        {"o": org_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request), action="platform.organization.reactivated",
        entity_type="organization", entity_id=str(org_id),
        organization_id=org_id, actor_subject=admin.subject,
        before={"status": org["status"]}, after={"status": "active"},
    )
    return {"id": str(org_id), "status": "active"}


class TenantCreate(BaseModel):
    """A new tenant, its first property and the person who will own it.

    Carries no organisation id: there is no organisation yet, which is why
    this is the one platform route that cannot take one in its path.
    """

    organization_name: str = Field(min_length=2, max_length=200)
    property_name: str | None = Field(default=None, max_length=200)
    owner_name: str = Field(min_length=2, max_length=150)
    owner_email: str = Field(min_length=5, max_length=200)
    owner_phone: str | None = Field(default=None, max_length=40)
    timezone: str = "Asia/Kolkata"
    currency: str = "INR"


@platform_router.post("/organizations", status_code=201)
def create_tenant(
    request: Request,
    body: TenantCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.create")),
):
    """Provision a tenant the way self-signup does, but on someone's behalf.

    Same seeded roles and property defaults as ``/auth/sign-up``, so a tenant
    created here is indistinguishable from one that signed itself up. The
    owner gets a set-password link rather than a password: nobody, including
    the platform, should ever know a customer's credential.
    """
    email = _normalise_email(body.owner_email)
    if db.execute(text("SELECT 1 FROM iam.users WHERE lower(email) = :em"),
                  {"em": email}).first():
        raise HTTPException(
            status_code=409,
            detail="An account already exists for that address.")

    org_id, property_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    membership_id = uuid.uuid4()
    code = _new_property_code(db)
    prop_name = (body.property_name or "").strip() or body.organization_name
    subject = f"{email.split('@')[0]}-{uuid.uuid4().hex[:6]}"

    db.execute(
        text("INSERT INTO iam.organizations (id, name) VALUES (:id, :name)"),
        {"id": org_id, "name": body.organization_name.strip()},
    )
    db.execute(
        text(
            """
            INSERT INTO iam.properties
                (id, organization_id, code, name, timezone, currency, status)
            VALUES (:id, :org, :code, :name, :tz, :cur, 'active')
            """
        ),
        {"id": property_id, "org": org_id, "code": code, "name": prop_name,
         "tz": body.timezone, "cur": body.currency},
    )
    db.execute(
        text(
            """
            INSERT INTO iam.users
                (id, identity_provider, subject_id, display_name, status,
                 email, phone)
            VALUES (:id, 'local', :sub, :name, 'invited', :em, :phone)
            """
        ),
        {"id": user_id, "sub": subject, "name": body.owner_name.strip(),
         "em": email, "phone": (body.owner_phone or "").strip() or None},
    )
    db.execute(
        text("INSERT INTO iam.memberships (id, organization_id, user_id, "
             "status) VALUES (:id, :org, :u, 'active')"),
        {"id": membership_id, "org": org_id, "u": user_id},
    )
    role_id = _seed_roles(db, org_id)
    db.execute(
        text(
            """
            INSERT INTO iam.role_assignments
                (id, membership_id, role_id, property_id, scope_type)
            VALUES (gen_random_uuid(), :m, :r, :p, 'property')
            """
        ),
        {"m": membership_id, "r": role_id, "p": property_id},
    )
    db.execute(
        text("INSERT INTO iam.property_onboarding "
             "(property_id, organization_id, current_step) "
             "VALUES (:p, :org, 'property')"),
        {"p": property_id, "org": org_id},
    )
    _seed_property_defaults(db, org_id=org_id, property_id=property_id)

    token = issue_password_token(
        db, user_id=user_id, purpose="welcome", hours=WELCOME_HOURS)
    mail_error = ""
    subject_line = ""
    try:
        subject_line, text_body, html_body = owner_invitation_email(
            name=body.owner_name.strip(), property_name=prop_name,
            property_code=code, email=email, token=token,
            hours=WELCOME_HOURS)
        # send() takes text= and html=. This call passed text_body= and
        # html_body= and so raised TypeError before reaching the mail server
        # at all -- past `except MailNotConfigured`, which never saw it, and
        # out through a route that had already created the tenant. Every
        # tenant made here since was rolled back at the last step.
        send_mail(settings.mail_config, to=email, subject=subject_line,
                  text=text_body, html=html_body)
        emailed = True
    except MailNotConfigured:
        # The tenant exists either way. Reporting the failure is the point --
        # a silent one leaves an owner with no way in and nobody aware of it.
        emailed = False
    except (OSError, SMTPException) as exc:
        # A refused or unreachable mail server is the same story for the
        # caller: the tenant is real, the owner has not been told.
        emailed = False
        mail_error = str(exc)[:200]
    # After the response, which is after TransactionalRoute commits. Called
    # here directly it ran on its own connection *before* the organisation
    # existed anywhere but this transaction, so the row's foreign key failed
    # and the record was dropped -- silently, because recording never raises.
    background.add_task(
        record_delivery,
        SessionFactory, template_code="welcome", recipient=email,
        subject=subject_line if emailed else None, organization_id=org_id,
        status="sent" if emailed else "failed",
        detail=None if emailed else (mail_error or "no mail server configured"),
    )

    record_audit(
        db, correlation_id=correlation_id(request), action="platform.tenant.created", entity_type="organization",
        entity_id=str(org_id), organization_id=org_id,
        property_id=property_id, actor_subject=admin.subject,
        after={"organization": body.organization_name.strip(),
               "property_code": code, "owner_email": email,
               "welcome_emailed": emailed},
    )
    return {
        "organization_id": str(org_id), "property_id": str(property_id),
        "property_code": code, "owner_user_id": str(user_id),
        "owner_email": email, "welcome_emailed": emailed,
    }


# =============================================== correcting an invite =====

def _invited_member(db: Session, org_id, user_id) -> dict:
    """A person in this organisation who has not signed in yet.

    Both halves matter. The membership check stops one tenant's id being used
    to reach another's account, and `invited` is the line between fixing a
    mistake nobody has acted on and editing a live account -- which belongs to
    the tenant's own settings, not to platform staff.
    """
    row = db.execute(
        text(
            """
            SELECT u.id, u.display_name, u.email, u.status
            FROM iam.memberships m
            JOIN iam.users u ON u.id = m.user_id
            WHERE m.organization_id = :o AND u.id = :u
            """
        ),
        {"o": org_id, "u": user_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such person in this tenant.")
    if row["status"] != "invited":
        raise HTTPException(
            status_code=409,
            detail="This account has already been set up. Its owner changes "
                   "their own details from the application; platform staff "
                   "do not edit a live account.")
    return dict(row)


def _first_property(db: Session, org_id) -> dict:
    row = db.execute(
        text("SELECT id, code, name FROM iam.properties "
             "WHERE organization_id = :o ORDER BY created_at LIMIT 1"),
        {"o": org_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=409,
                            detail="This tenant has no property to invite into.")
    return dict(row)


class OwnerCorrection(BaseModel):
    """What may be corrected before the invitation is accepted."""

    email: str | None = Field(default=None, min_length=5, max_length=200)
    display_name: str | None = Field(default=None, min_length=2, max_length=150)
    # Required. Redirecting somebody's sign-in link is exactly the kind of
    # change the tenant's audit log should be able to explain.
    reason: str = Field(min_length=3, max_length=300)


@platform_router.patch("/organizations/{org_id}/users/{user_id}")
def correct_invited_user(
    request: Request,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body: OwnerCorrection,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("user.lifecycle")),
):
    """Fix the address a tenant's invitation was sent to.

    Only while the account is still `invited`, and every outstanding link is
    burnt in the same breath: a token issued to the wrong address must stop
    working the moment the address is corrected, or the typo'd mailbox keeps a
    live way in.
    """
    _org_or_404(db, org_id)
    before = _invited_member(db, org_id, user_id)

    email = _normalise_email(body.email) if body.email else before["email"]
    name = (body.display_name or "").strip() or before["display_name"]
    if email == before["email"] and name == before["display_name"]:
        raise HTTPException(status_code=422, detail="Nothing was changed.")

    if email != before["email"]:
        taken = db.execute(
            text("SELECT 1 FROM iam.users WHERE lower(email) = :em AND id <> :u"),
            {"em": email, "u": user_id},
        ).first()
        if taken:
            raise HTTPException(
                status_code=409,
                detail="An account already exists for that address.")

    db.execute(
        text("UPDATE iam.users SET email = :em, display_name = :n, "
             "updated_at = now() WHERE id = :u"),
        {"em": email, "n": name, "u": user_id},
    )
    # Every link already sent, whatever its purpose. The old address keeps the
    # message but not the way in.
    burnt = db.execute(
        text("UPDATE iam.password_tokens SET used_at = now() "
             "WHERE user_id = :u AND used_at IS NULL"),
        {"u": user_id},
    ).rowcount

    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.invited_user.corrected", entity_type="user",
        entity_id=str(user_id), organization_id=org_id,
        actor_subject=admin.subject, reason=body.reason,
        before={"email": before["email"], "display_name": before["display_name"]},
        after={"email": email, "display_name": name, "links_invalidated": burnt},
    )
    return {"id": str(user_id), "email": email, "display_name": name,
            "links_invalidated": burnt}


@platform_router.post("/organizations/{org_id}/users/{user_id}/resend-invite")
def resend_invitation(
    request: Request,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("user.lifecycle")),
):
    """Send the set-password link again, on a fresh token.

    `issue_password_token` retires the previous one, so a resend never leaves
    two working links in two inboxes.
    """
    _org_or_404(db, org_id)
    person = _invited_member(db, org_id, user_id)
    prop = _first_property(db, org_id)

    token = issue_password_token(
        db, user_id=user_id, purpose="welcome", hours=WELCOME_HOURS)
    mail_error = ""
    subject_line = ""
    try:
        subject_line, text_body, html_body = owner_invitation_email(
            name=person["display_name"], property_name=prop["name"],
            property_code=prop["code"], email=person["email"], token=token,
            hours=WELCOME_HOURS)
        send_mail(settings.mail_config, to=person["email"],
                  subject=subject_line, text=text_body, html=html_body)
        emailed = True
    except MailNotConfigured:
        emailed = False
    except (OSError, SMTPException) as exc:
        emailed = False
        mail_error = str(exc)[:200]

    background.add_task(
        record_delivery,
        SessionFactory, template_code="welcome", recipient=person["email"],
        subject=subject_line if emailed else None, organization_id=org_id,
        status="sent" if emailed else "failed",
        detail=None if emailed else (mail_error or "no mail server configured"),
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.invitation.resent", entity_type="user",
        entity_id=str(user_id), organization_id=org_id,
        property_id=prop["id"], actor_subject=admin.subject,
        after={"email": person["email"], "emailed": emailed,
               "hours": WELCOME_HOURS},
    )
    return {"email": person["email"], "emailed": emailed,
            "hours": WELCOME_HOURS,
            "detail": mail_error or ("" if emailed else "no mail server configured")}


# ================================================= deleting a tenant =====
#
# Four separate acts by design: survey, request, approve, carry out. Suspend
# is the reversible answer and stays the default -- this exists so that the
# irreversible one, when it is genuinely wanted, happens on the record instead
# of in a database client.

#: How long a request must sit before it can be carried out. A deletion that
#: can be completed in the same minute it was thought of is one bad afternoon
#: away from being permanent.
DELETION_COOLING_HOURS = 24


@platform_router.get("/organizations/{org_id}/deletion-survey")
def survey_tenant_deletion(
    org_id: uuid.UUID,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.delete")),
):
    """Exactly what would be destroyed. Reads only.

    Separate from the request so the number is seen before the reason is
    written, rather than after.
    """
    org = _org_or_404(db, org_id)
    held = survey(db, org_id)
    return {
        "organization": {"id": str(org_id), "code": org.get("code"),
                         "name": org["name"], "status": org["status"]},
        "tables": held,
        "rows_total": sum(held.values()),
        "suspended": org["status"] == "suspended",
        "cooling_hours": DELETION_COOLING_HOURS,
    }


class DeletionRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=400)
    #: The tenant's own code, typed by the operator. A request that cannot
    #: name its target was not deliberate enough to be a deletion request.
    confirm_code: str = Field(min_length=1, max_length=12)


@platform_router.post("/organizations/{org_id}/deletion-requests",
                      status_code=201)
def request_tenant_deletion(
    request: Request,
    org_id: uuid.UUID,
    body: DeletionRequest,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.delete")),
):
    """Ask for a tenant to be deleted. Does not delete anything."""
    org = _org_or_404(db, org_id)

    if (org.get("code") or "").strip().upper() != body.confirm_code.strip().upper():
        raise HTTPException(
            status_code=422,
            detail=f"That is not this tenant's code. Type {org.get('code')} "
                   "to confirm which tenant is being deleted.")

    # Deletion is never the first thing that happens to a tenant. Suspending
    # first is reversible, takes effect immediately, and gives anybody who
    # still needs the account a chance to say so.
    if org["status"] != "suspended":
        raise HTTPException(
            status_code=409,
            detail="Suspend this tenant first. Suspension is reversible and "
                   "takes effect at once; deletion is neither.")

    existing = _one(
        db,
        "SELECT id, status FROM platform.tenant_deletions "
        "WHERE organization_id = :o AND status IN ('requested', 'approved')",
        {"o": org_id})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A deletion request for this tenant is already {existing['status']}.")

    held = survey(db, org_id)
    row = _one(
        db,
        """
        INSERT INTO platform.tenant_deletions
            (organization_id, organization_code, organization_name, reason,
             requested_by, executable_after, removed)
        VALUES (:o, :code, :name, :why, :by,
                now() + make_interval(hours => :hrs), CAST(:held AS jsonb))
        RETURNING id, status, requested_at, executable_after
        """,
        {"o": org_id, "code": org.get("code"), "name": org["name"],
         "why": body.reason.strip(), "by": admin.user_id,
         "hrs": DELETION_COOLING_HOURS, "held": json.dumps(held)},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.tenant.deletion_requested", entity_type="organization",
        entity_id=str(org_id), organization_id=org_id,
        actor_subject=admin.subject, reason=body.reason.strip(),
        after={"request_id": str(row["id"]), "rows": sum(held.values()),
               "executable_after": str(row["executable_after"])},
    )
    return {"id": str(row["id"]), "status": row["status"],
            "executable_after": row["executable_after"],
            "rows_to_remove": sum(held.values()),
            "needs": "approval by somebody other than the requester"}


@platform_router.get("/deletion-requests")
def list_deletion_requests(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.view")),
):
    """Every deletion request, including ones whose tenant is already gone."""
    return _rows(
        db,
        """
        SELECT d.id, d.organization_id, d.organization_code, d.organization_name,
               d.reason, d.status, d.requested_at, d.executable_after,
               d.requested_by,
               d.approved_at, d.completed_at, d.export_location,
               r.display_name AS requested_by_name,
               a.display_name AS approved_by_name,
               (SELECT count(*) FROM iam.organizations o
                 WHERE o.id = d.organization_id) > 0 AS tenant_exists,
               d.removed
        FROM platform.tenant_deletions d
        LEFT JOIN iam.users r ON r.id = d.requested_by
        LEFT JOIN iam.users a ON a.id = d.approved_by
        ORDER BY d.requested_at DESC
        """,
    )


@platform_router.post("/deletion-requests/{request_id}/withdraw")
def withdraw_deletion_request(
    request: Request,
    request_id: uuid.UUID,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.delete")),
):
    """Call off a request. Always available while it is still open."""
    row = _one(db, "SELECT * FROM platform.tenant_deletions WHERE id = :i",
               {"i": request_id})
    if row is None:
        raise HTTPException(status_code=404, detail="No such request.")
    if row["status"] not in ("requested", "approved"):
        raise HTTPException(status_code=409,
                            detail=f"This request is already {row['status']}.")
    db.execute(
        text("UPDATE platform.tenant_deletions SET status = 'cancelled' "
             "WHERE id = :i"), {"i": request_id})
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.tenant.deletion_cancelled", entity_type="organization",
        entity_id=str(row["organization_id"]),
        organization_id=row["organization_id"], actor_subject=admin.subject,
        after={"request_id": str(request_id)},
    )
    return {"id": str(request_id), "status": "cancelled"}


class DeletionApproval(BaseModel):
    #: Typed again by the approver. The person carrying this out should have
    #: looked at which tenant it is, not only at the request.
    confirm_code: str = Field(min_length=1, max_length=12)


@platform_router.post("/deletion-requests/{request_id}/approve")
def approve_tenant_deletion(
    request: Request,
    request_id: uuid.UUID,
    body: DeletionApproval,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.delete_approve")),
):
    """Approve and carry out a deletion.

    One transaction. The purge refuses unless nothing anywhere still points at
    the tenant, and a refusal takes the approval down with it -- so the
    request stays open and the tenant stays whole, rather than the database
    keeping a half-finished deletion.
    """
    row = _one(db, "SELECT * FROM platform.tenant_deletions WHERE id = :i",
               {"i": request_id})
    if row is None:
        raise HTTPException(status_code=404, detail="No such request.")
    if row["status"] != "requested":
        raise HTTPException(status_code=409,
                            detail=f"This request is {row['status']}.")
    # The two-person rule, enforced where the number of people is knowable.
    #
    # It used to be a CHECK constraint, which cannot see how many platform
    # admins exist -- so it demanded a separate approver even where no second
    # person existed to be one. Combined with "a completed row must name an
    # approver", that made deletion not merely hard but impossible on a
    # single-admin deployment, and impossible to record afterwards either.
    #
    # Counting here gives the rule its actual meaning: insist on a second
    # person whenever there is a second person. A deployment that gains one
    # gets the rule back with no migration and nothing to remember.
    others = _one(
        db,
        "SELECT count(*) AS n FROM iam.platform_admins "
        "WHERE status = 'active' AND user_id <> :me",
        {"me": admin.user_id})
    sole_admin = int((others or {}).get("n", 0)) == 0
    if row["requested_by"] == admin.user_id and not sole_admin:
        raise HTTPException(
            status_code=403,
            detail="A deletion cannot be approved by the person who asked for "
                   "it. Somebody else has to look at it.")
    if row["executable_after"] > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=409,
            detail=f"Not before {row['executable_after']:%d %b %Y %H:%M} UTC. "
                   "The waiting period is what makes this reversible until "
                   "then.")
    if (row["organization_code"] or "").strip().upper() != body.confirm_code.strip().upper():
        raise HTTPException(
            status_code=422,
            detail=f"That is not the tenant's code. Type "
                   f"{row['organization_code']} to confirm.")

    org = _one(db, "SELECT id, name, status FROM iam.organizations WHERE id = :o",
               {"o": row["organization_id"]})
    if org is None:
        raise HTTPException(status_code=409,
                            detail="That tenant no longer exists.")

    # The audit row is written BEFORE the tenant goes. iam.audit_events has no
    # foreign key to organizations, so it survives -- but writing it first
    # means it exists even if something below fails and rolls back, which is
    # the wrong way round only if you would rather have no record at all.
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.tenant.deleted", entity_type="organization",
        entity_id=str(row["organization_id"]),
        organization_id=None,          # the tenant is about to stop existing
        actor_subject=admin.subject, reason=row["reason"],
        before={"code": row["organization_code"], "name": org["name"],
                "held": row["removed"]},
        after={"request_id": str(request_id),
               "requested_by": str(row["requested_by"]),
               "approved_by": str(admin.user_id),
               # Stated, never inferred later from two ids being equal. A
               # deletion nobody else reviewed should say so in the record.
               "review": "sole platform admin — no second approver existed"
                         if sole_admin else "approved by a second person"},
    )

    # The export goes first, and a failure here stops the deletion. A tenant
    # removed without one is the situation this whole pathway exists to
    # prevent -- the safety net is not optional just because the upload is the
    # part most likely to be unavailable.
    try:
        blob, counts = export_tenant(db, row["organization_id"])
        key = (f"tenant-exports/{row['organization_code'] or 'unknown'}"
               f"/{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json.gz")
        put_archive(_STORE, key, blob)
        # Read it back before destroying the source. An upload that returned
        # without error is not the same as an object somebody can fetch, and
        # the difference only ever shows up when the export is needed.
        if get_archive(_STORE, key) != blob:
            raise ObjectStoreError(
                "the stored export does not match what was uploaded")
    except (ObjectStoreError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"The tenant was not deleted: its export could not be "
                   f"stored ({str(exc)[:160]}).") from None

    # organization_id is deliberately left null. In a moment there will be no
    # organisation for it to point at, and a foreign key to a row about to be
    # deleted would either block the deletion or take the record with it. The
    # label carries the code and name, which is what a person reads anyway.
    snapshot = _one(
        db,
        """
        INSERT INTO platform.backup_snapshots
            (label, scope, organization_id, taken_at, size_bytes, location,
             verified_at, verify_detail)
        VALUES (:label, 'tenant', NULL, now(), :size, :loc, now(), :detail)
        RETURNING id
        """,
        {"label": f"{row['organization_code']} {row['organization_name']}"
                  " — export taken before deletion",
         "size": len(blob), "loc": key,
         "detail": f"{sum(counts.values())} rows across {len(counts)} tables, "
                   "written and read back at export time"},
    )

    try:
        result = purge(db, row["organization_id"])
    except RuntimeError as exc:
        # The transaction rolls back, so the snapshot row goes too. The object
        # in the bucket stays -- an export of a tenant that still exists is
        # harmless, and deleting it here would mean unwinding the one artefact
        # worth keeping if anything is wrong.
        raise HTTPException(status_code=409, detail=str(exc)) from None

    # Checked, not assumed. The first version of this swept the deletions
    # table along with the tenant, so this UPDATE matched nothing and the
    # endpoint still reported success -- a deletion with no record of itself.
    marked = db.execute(
        text("""UPDATE platform.tenant_deletions
                   SET status = 'completed', approved_by = :by,
                       approved_at = now(), completed_at = now(),
                       removed = CAST(:removed AS jsonb),
                       export_location = :loc
                 WHERE id = :i"""),
        {"by": admin.user_id, "i": request_id, "loc": key,
         "removed": json.dumps(result["removed"])},
    ).rowcount
    if marked != 1:
        raise HTTPException(
            status_code=500,
            detail="The tenant was removed but the deletion could not be "
                   "recorded; nothing was kept.")
    return {"id": str(request_id), "status": "completed",
            "organization": row["organization_name"],
            "rows_removed": sum(result["removed"].values()),
            "tables": result["removed"],
            "export_location": key,
            "export_bytes": len(blob),
            "snapshot_id": str(snapshot["id"]) if snapshot else None}


# ========================================================== properties =====

@platform_router.get("/properties")
def list_properties(
    org_id: uuid.UUID | None = None,
    query: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("property.view")),
):
    """Every property on the platform, searchable by name or property code.

    The code is what a caller to support reads out, so it is the search key
    that matters most.
    """
    return _rows(
        db,
        """
        SELECT p.id, p.code, p.name, p.status, p.timezone, p.currency,
               p.contact_email, p.city, p.state,
               p.organization_id, o.name AS organization_name,
               o.code AS organization_code, o.status AS organization_status,
               ob.current_step, ob.activated_at,
               (SELECT count(*) FROM property.rooms r
                 WHERE r.property_id = p.id) AS rooms,
               -- What the pack calls "State" is the property's readiness, not
               -- its row status: live, still in setup, or suspended. Three
               -- different facts live in two columns, so they are resolved
               -- here rather than by each caller guessing.
               CASE WHEN p.status <> 'active' THEN p.status
                    WHEN ob.activated_at IS NOT NULL THEN 'live'
                    ELSE 'setup' END AS readiness
        FROM iam.properties p
        JOIN iam.organizations o ON o.id = p.organization_id
        LEFT JOIN iam.property_onboarding ob ON ob.property_id = p.id
        WHERE (CAST(:org AS uuid) IS NULL OR p.organization_id = :org)
          AND (CAST(:q AS text) IS NULL OR p.name ILIKE '%' || :q || '%'
               OR upper(p.code) = upper(:q))
          AND (CAST(:state AS text) IS NULL
               OR CASE WHEN p.status <> 'active' THEN p.status
                       WHEN ob.activated_at IS NOT NULL THEN 'live'
                       ELSE 'setup' END = :state)
        ORDER BY o.name, p.name
        """,
        {"org": org_id, "q": query or None, "state": state or None},
    )


class PropertyStatusIn(BaseModel):
    status: str = Field(pattern="^(active|suspended)$")
    reason: str = Field(min_length=3, max_length=300)


@platform_router.post("/properties/{property_id}/status")
def set_property_status(
    request: Request,
    property_id: uuid.UUID,
    body: PropertyStatusIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("property.lifecycle")),
):
    row = _one(
        db,
        "SELECT id, organization_id, name, status FROM iam.properties "
        "WHERE id = :p",
        {"p": property_id},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Property not found")
    db.execute(
        text("UPDATE iam.properties SET status = :s, updated_at = now(), "
             "version = version + 1 WHERE id = :p"),
        {"s": body.status, "p": property_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request), action=f"platform.property.{body.status}",
        entity_type="property", entity_id=str(property_id),
        organization_id=row["organization_id"], property_id=property_id,
        actor_subject=admin.subject, reason=body.reason,
        before={"status": row["status"]}, after={"status": body.status},
    )
    return {"id": str(property_id), "status": body.status}


@platform_router.get("/properties/{property_id}/modules")
def list_modules(
    property_id: uuid.UUID,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("property.view")),
):
    """What this property is entitled to. The table already existed, unused."""
    if _one(db, "SELECT id FROM iam.properties WHERE id = :p",
            {"p": property_id}) is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return _rows(
        db,
        "SELECT module_code, enabled, enabled_at FROM iam.property_modules "
        "WHERE property_id = :p ORDER BY module_code",
        {"p": property_id},
    )


class ModuleIn(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None, max_length=300)


@platform_router.put("/properties/{property_id}/modules/{module_code}")
def set_module(
    request: Request,
    property_id: uuid.UUID,
    module_code: str,
    body: ModuleIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("property.entitlements")),
):
    row = _one(
        db,
        "SELECT id, organization_id FROM iam.properties WHERE id = :p",
        {"p": property_id},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Property not found")
    before = _one(
        db,
        "SELECT enabled FROM iam.property_modules "
        "WHERE property_id = :p AND module_code = :m",
        {"p": property_id, "m": module_code},
    )
    db.execute(
        text(
            """
            INSERT INTO iam.property_modules
                (property_id, module_code, enabled, enabled_at)
            VALUES (:p, :m, :e, CASE WHEN :e THEN now() END)
            ON CONFLICT (property_id, module_code) DO UPDATE
               SET enabled = EXCLUDED.enabled,
                   enabled_at = CASE WHEN EXCLUDED.enabled
                        THEN COALESCE(iam.property_modules.enabled_at, now())
                        END,
                   updated_at = now()
            """
        ),
        {"p": property_id, "m": module_code, "e": body.enabled},
    )
    record_audit(
        db, correlation_id=correlation_id(request), action="platform.module.changed", entity_type="property_module",
        entity_id=f"{property_id}:{module_code}",
        organization_id=row["organization_id"], property_id=property_id,
        actor_subject=admin.subject, reason=body.reason,
        before=before or {}, after={"enabled": body.enabled},
    )
    return {"property_id": str(property_id), "module_code": module_code,
            "enabled": body.enabled}


# =============================================================== users =====

@platform_router.get("/users")
def find_users(
    query: str = Query(min_length=3),
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("user.view")),
):
    """Find a person by email or name across every tenant.

    A minimum query length rather than a blank listing: this is a lookup for
    somebody who has told you who they are, not a directory of every user on
    the platform to be paged through.
    """
    return _rows(
        db,
        """
        SELECT u.id, u.display_name, u.email, u.status, u.last_login_at,
               u.mfa_status,
               (SELECT count(*) FROM iam.platform_admins pa
                 WHERE pa.user_id = u.id AND pa.status = 'active') > 0
                   AS is_platform_admin,
               COALESCE(
                 (SELECT json_agg(json_build_object(
                            'organization_id', o.id, 'organization', o.name,
                            'organization_status', o.status,
                            'membership_status', m.status))
                    FROM iam.memberships m
                    JOIN iam.organizations o ON o.id = m.organization_id
                   WHERE m.user_id = u.id), '[]'::json) AS memberships
        FROM iam.users u
        WHERE lower(u.email) LIKE '%' || lower(:q) || '%'
           OR u.display_name ILIKE '%' || :q || '%'
           OR u.subject_id = :q
        ORDER BY u.display_name
        LIMIT 50
        """,
        {"q": query.strip()},
    )


class UserStatusIn(BaseModel):
    status: str = Field(pattern="^(active|suspended)$")
    reason: str = Field(min_length=3, max_length=300)


@platform_router.post("/users/{user_id}/status")
def set_user_status(
    request: Request,
    user_id: uuid.UUID,
    body: UserStatusIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("user.lifecycle")),
):
    """Deactivate or restore a person platform-wide.

    Platform-wide because the reasons for reaching this route -- a compromised
    account, someone who has left under a cloud -- are not confined to one
    tenant, and a person suspended in one organisation while still active in
    another is the gap that makes the action pointless.
    """
    row = _one(
        db,
        "SELECT id, display_name, email, status FROM iam.users WHERE id = :u",
        {"u": user_id},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == admin.user_id:
        raise HTTPException(
            status_code=409,
            detail="You cannot change your own account status here.")

    db.execute(
        text("UPDATE iam.users SET status = :s, updated_at = now(), "
             "version = version + 1 WHERE id = :u"),
        {"s": body.status, "u": user_id},
    )
    revoked = 0
    if body.status != "active":
        revoked = db.execute(
            text("UPDATE iam.login_sessions SET revoked_at = now() "
                 "WHERE user_id = :u AND revoked_at IS NULL"),
            {"u": user_id},
        ).rowcount
    record_audit(
        db, correlation_id=correlation_id(request), action=f"platform.user.{body.status}", entity_type="user",
        entity_id=str(user_id), actor_subject=admin.subject,
        reason=body.reason, before={"status": row["status"]},
        after={"status": body.status, "sessions_revoked": revoked},
    )
    return {"id": str(user_id), "status": body.status,
            "sessions_revoked": revoked}


class RevokeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@platform_router.post("/users/{user_id}/revoke-sessions")
def revoke_sessions(
    request: Request,
    user_id: uuid.UUID,
    body: RevokeIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("user.sessions")),
):
    """Sign a person out everywhere, without changing their account."""
    if _one(db, "SELECT id FROM iam.users WHERE id = :u",
            {"u": user_id}) is None:
        raise HTTPException(status_code=404, detail="User not found")
    revoked = db.execute(
        text("UPDATE iam.login_sessions SET revoked_at = now() "
             "WHERE user_id = :u AND revoked_at IS NULL"),
        {"u": user_id},
    ).rowcount
    record_audit(
        db, correlation_id=correlation_id(request), action="platform.user.sessions_revoked", entity_type="user",
        entity_id=str(user_id), actor_subject=admin.subject,
        reason=body.reason, after={"sessions_revoked": revoked},
    )
    return {"id": str(user_id), "sessions_revoked": revoked}


# =========================================================== catalogue =====

@platform_router.get("/permissions")
def list_permissions(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("catalog.view")),
):
    """The global permission catalogue, with how widely each is granted.

    Global is the literal truth: ``iam.permissions`` has no organisation
    column. Until now only a migration could add a row to it, which is how a
    route shipped gated on ``property.create`` while the permission itself did
    not exist -- every tenant got 403 on a button they could see.
    """
    return _rows(
        db,
        """
        SELECT p.id, p.resource_code, p.action_code, p.description,
               (SELECT count(*) FROM iam.role_permissions rp
                 WHERE rp.permission_id = p.id) AS granted_to_roles
        FROM iam.permissions p
        ORDER BY p.resource_code, p.action_code
        """,
    )


class PermissionIn(BaseModel):
    resource_code: str = Field(min_length=2, max_length=100,
                               pattern="^[a-z][a-z0-9_]*$")
    action_code: str = Field(min_length=2, max_length=100,
                             pattern="^[a-z][a-z0-9_]*$")
    description: str | None = Field(default=None, max_length=300)


@platform_router.post("/permissions", status_code=201)
def create_permission(
    request: Request,
    body: PermissionIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("catalog.manage")),
):
    """Add a permission to the catalogue. It grants nothing by itself.

    Creating the row and granting it to a role are separate on purpose: a new
    permission that arrived pre-granted would widen every role holding it
    without anybody choosing that.
    """
    if _one(db,
            "SELECT id FROM iam.permissions WHERE resource_code = :r "
            "AND action_code = :a",
            {"r": body.resource_code, "a": body.action_code}):
        raise HTTPException(status_code=409, detail="Permission already exists")
    pid = uuid.uuid4()
    db.execute(
        text("INSERT INTO iam.permissions (id, resource_code, action_code, "
             "description) VALUES (:id, :r, :a, :d)"),
        {"id": pid, "r": body.resource_code, "a": body.action_code,
         "d": body.description},
    )
    record_audit(
        db, correlation_id=correlation_id(request), action="platform.permission.created", entity_type="permission",
        entity_id=str(pid), actor_subject=admin.subject,
        after={"resource_code": body.resource_code,
               "action_code": body.action_code},
    )
    return {"id": str(pid), "resource_code": body.resource_code,
            "action_code": body.action_code, "granted_to_roles": 0}


# ======================================================= observability =====

@platform_router.get("/audit")
def platform_audit(
    org_id: uuid.UUID | None = None,
    action: str | None = None,
    actor: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("audit.view")),
):
    """The audit trail across tenants, including the platform's own actions.

    Snapshots are deliberately not returned -- only whether one exists. The
    before/after of a tenant's own change can contain their data, and this
    route is read by people outside that tenant.
    """
    return _rows(
        db,
        """
        SELECT a.id, a.occurred_at, a.actor_subject, a.action, a.entity_type,
               a.entity_id, a.reason, a.organization_id,
               o.name AS organization_name, a.property_id,
               (a.redacted_before IS NOT NULL) AS has_before,
               (a.redacted_after IS NOT NULL) AS has_after
        FROM iam.audit_events a
        LEFT JOIN iam.organizations o ON o.id = a.organization_id
        WHERE (CAST(:org AS uuid) IS NULL OR a.organization_id = :org)
          AND (CAST(:act AS text) IS NULL OR a.action ILIKE '%' || :act || '%')
          AND (CAST(:who AS text) IS NULL OR a.actor_subject ILIKE '%' || :who || '%')
          AND (CAST(:df AS date) IS NULL OR a.occurred_at >= CAST(:df AS date))
          AND (CAST(:dt AS date) IS NULL
               OR a.occurred_at < CAST(:dt AS date) + 1)
        ORDER BY a.occurred_at DESC
        LIMIT :lim
        """,
        {"org": org_id, "act": action or None, "who": actor or None,
         "df": date_from, "dt": date_to, "lim": limit},
    )


@platform_router.get("/usage")
def usage(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.view")),
):
    """How much of the platform each tenant is actually using.

    Counts only. No revenue, no balances: what a customer's hotel takes is
    their business, and a platform operator does not need it to run a server.
    """
    system_context(db, reason="platform: usage counts per tenant")
    return _rows(
        db,
        """
        SELECT o.id AS organization_id, o.name AS organization_name, o.status,
               (SELECT count(*) FROM iam.properties p
                 WHERE p.organization_id = o.id) AS properties,
               (SELECT count(*) FROM iam.memberships m
                 WHERE m.organization_id = o.id
                   AND m.status = 'active') AS active_users,
               (SELECT count(*) FROM booking.reservations r
                 WHERE r.organization_id = o.id) AS reservations,
               (SELECT count(*) FROM finance.folios f
                 WHERE f.organization_id = o.id) AS folios,
               (SELECT max(r.created_at) FROM booking.reservations r
                 WHERE r.organization_id = o.id) AS last_reservation_at
        FROM iam.organizations o
        ORDER BY o.name
        """,
    )


@platform_router.get("/business-dates")
def business_dates(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("operations.view")),
):
    """Where every property's business date has got to, and whether it drifted.

    This exists because it happened: a property ran a full calendar day ahead
    of itself, every report quietly answered for the wrong day, and nothing
    anywhere surfaced it. ``days_ahead`` is the number that would have.
    """
    system_context(db, reason="platform: business dates per property")
    return _rows(
        db,
        """
        SELECT p.id AS property_id, p.code, p.name, p.timezone,
               o.name AS organization_name,
               s.audit_hour,
               last.business_date AS last_audited_date,
               last.status AS last_run_status,
               last.completed_at,
               (last.business_date
                  - (now() AT TIME ZONE p.timezone)::date) AS days_ahead
        FROM iam.properties p
        JOIN iam.organizations o ON o.id = p.organization_id
        LEFT JOIN finance.night_audit_settings s ON s.property_id = p.id
        LEFT JOIN LATERAL (
            SELECT r.business_date, r.status, r.completed_at
            FROM finance.night_audit_runs r
            WHERE r.property_id = p.id
            ORDER BY r.business_date DESC, r.run_number DESC
            LIMIT 1
        ) last ON true
        WHERE p.status = 'active'
        ORDER BY o.name, p.name
        """,
    )


@platform_router.get("/ota-actions")
def ota_actions_across_properties(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("operations.view")),
):
    """What every property owes its channels, and how much is already late.

    A no-show on an OTA booking has to be reported to that channel within 24
    hours or the hotel pays commission on a room nobody slept in. Each
    property can see its own queue. Nobody could see them together, and
    together is the only view in which the pattern shows: one hotel quietly
    missing its window every week costs more over a year than a single
    forgotten booking ever does, and from inside that hotel it looks like
    nothing at all.

    ``overdue`` is the number to act on. ``oldest_overdue_hours`` says how bad
    the worst one is, because ten obligations an hour late and one a fortnight
    late are different problems with the same count.

    Properties with nothing owed are listed with zeros rather than omitted. An
    empty row is the evidence that a hotel is keeping up; dropping it would
    make "no rows" ambiguous between "all clear" and "not reporting".
    """
    system_context(db, reason="platform: OTA obligations per property")
    return _rows(
        db,
        """
        SELECT p.id AS property_id, p.code, p.name,
               o.name AS organization_name,
               COALESCE(a.open_count, 0) AS open_count,
               COALESCE(a.overdue_count, 0) AS overdue_count,
               COALESCE(a.reported_count, 0) AS reported_count,
               a.next_due_at,
               a.oldest_overdue_hours
          FROM iam.properties p
          JOIN iam.organizations o ON o.id = p.organization_id
          LEFT JOIN LATERAL (
              SELECT count(*) FILTER (WHERE status = 'open') AS open_count,
                     count(*) FILTER (WHERE status = 'open'
                                        AND due_at < now()) AS overdue_count,
                     count(*) FILTER (WHERE status = 'reported')
                         AS reported_count,
                     min(due_at) FILTER (WHERE status = 'open'
                                           AND due_at >= now()) AS next_due_at,
                     -- How late the worst one is, in hours.
                     max(EXTRACT(EPOCH FROM (now() - due_at)) / 3600.0)
                         FILTER (WHERE status = 'open' AND due_at < now())
                         AS oldest_overdue_hours
                FROM distribution.ota_actions x
               WHERE x.property_id = p.id
          ) a ON true
         WHERE p.status = 'active'
         ORDER BY COALESCE(a.overdue_count, 0) DESC,
                  COALESCE(a.open_count, 0) DESC,
                  o.name, p.name
        """,
    )


@platform_router.get("/health")
def platform_health(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("operations.view")),
):
    """Whether the platform itself is in a fit state, in one call.

    Migration heads per *service*, not per schema. Three services own
    migrations between them and create eight schemas; reporting a head for a
    schema that was never independently versioned reads as a broken
    deployment when nothing is wrong.
    """
    heads: dict[str, str | None] = {}
    for owner in MIGRATION_OWNERS:
        exists = db.execute(
            text("SELECT 1 FROM information_schema.tables "
                 "WHERE table_schema = :s AND table_name = 'alembic_version'"),
            {"s": owner},
        ).first()
        heads[owner] = db.execute(
            text(f'SELECT version_num FROM "{owner}".alembic_version LIMIT 1')
        ).scalar() if exists else None

    totals = _one(
        db,
        """
        SELECT (SELECT count(*) FROM iam.organizations) AS organizations,
               (SELECT count(*) FROM iam.organizations
                 WHERE status = 'active') AS active_organizations,
               (SELECT count(*) FROM iam.properties) AS properties,
               (SELECT count(*) FROM iam.users
                 WHERE status = 'active') AS active_users,
               (SELECT count(*) FROM iam.platform_admins
                 WHERE status = 'active') AS platform_admins,
               (SELECT count(*) FROM iam.login_sessions
                 WHERE revoked_at IS NULL AND expires_at > now())
                   AS live_sessions
        """,
        {},
    )
    return {"migration_heads": heads, "totals": totals}


# ===================================================== platform admins =====

@platform_router.get("/admins")
def list_admins(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("staff.view")),
):
    """Who holds this level. A short list that should stay short."""
    return _rows(
        db,
        """
        SELECT pa.id, pa.user_id, u.display_name, u.email, pa.status,
               pa.granted_at, pa.revoked_at, pa.note,
               u.last_login_at AS last_active,
               -- The pack's MFA column. 'none' is a fact worth showing
               -- plainly: an operator without a second factor is the row
               -- somebody should act on.
               coalesce(m.status, 'none') AS mfa,
               g.display_name AS granted_by_name,
               coalesce(array_agg(r.code ORDER BY r.code)
                        FILTER (WHERE r.code IS NOT NULL), '{}') AS roles
        FROM iam.platform_admins pa
        JOIN iam.users u ON u.id = pa.user_id
        LEFT JOIN iam.user_mfa m ON m.user_id = pa.user_id
        LEFT JOIN iam.users g ON g.id = pa.granted_by
        LEFT JOIN iam.platform_role_assignments ra
               ON ra.platform_admin_id = pa.id
        LEFT JOIN iam.platform_roles r ON r.id = ra.role_id
        GROUP BY pa.id, pa.user_id, u.display_name, u.email, pa.status,
                 pa.granted_at, pa.revoked_at, pa.note, g.display_name,
                 u.last_login_at, m.status
        ORDER BY pa.status, u.display_name
        """,
    )


class AdminGrant(BaseModel):
    email: str = Field(min_length=5, max_length=200)
    note: str | None = Field(default=None, max_length=300)


@platform_router.post("/admins", status_code=201)
def grant_admin(
    request: Request,
    body: AdminGrant,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("staff.manage")),
):
    """Make an existing user platform staff.

    An existing user, not a new one: this route grants the highest privilege
    in the system and must not also be the route that creates an account.

    And an existing user who belongs to no tenant. A platform administrator
    with a membership is two principals wearing one identity: they would see
    every organisation through these routes and their own through the tenant
    routes, and no audit row could say which hat they had on. Worse, the
    temptation is then to treat "their" organisation as a default -- which is
    how a console that is supposed to name its target explicitly starts
    guessing one.

    Platform accounts are provisioned by scripts/grant_platform_admin.py
    --create, which makes a user with no membership and no organisation.
    """
    email = _normalise_email(body.email)
    user = _one(
        db,
        "SELECT id, display_name, status FROM iam.users "
        "WHERE lower(email) = :em",
        {"em": email},
    )
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="No user with that address. Create the account first.")
    if user["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail=f"That account is {user['status']}, not active.")

    # The tiers are separated by identity, not just by table.
    member_of = db.execute(
        text(
            """
            SELECT o.name
            FROM iam.memberships m
            JOIN iam.organizations o ON o.id = m.organization_id
            WHERE m.user_id = :u AND m.status = 'active'
            ORDER BY o.name
            """
        ),
        {"u": user["id"]},
    ).scalars().all()
    if member_of:
        raise HTTPException(
            status_code=409,
            detail=(
                f"That account belongs to {', '.join(member_of)}. A tenant "
                "user cannot hold platform access — create a separate "
                "platform account instead."
            ),
        )

    existing = _one(
        db,
        "SELECT id, status FROM iam.platform_admins WHERE user_id = :u",
        {"u": user["id"]},
    )
    if existing and existing["status"] == "active":
        raise HTTPException(status_code=409, detail="Already platform staff")
    if existing:
        db.execute(
            text("UPDATE iam.platform_admins SET status = 'active', "
                 "granted_by = :by, granted_at = now(), revoked_at = NULL, "
                 "note = :note, updated_at = now() WHERE id = :id"),
            {"by": admin.user_id, "note": body.note, "id": existing["id"]},
        )
        admin_id = existing["id"]
    else:
        admin_id = uuid.uuid4()
        db.execute(
            text("INSERT INTO iam.platform_admins "
                 "(id, user_id, status, granted_by, note) "
                 "VALUES (:id, :u, 'active', :by, :note)"),
            {"id": admin_id, "u": user["id"], "by": admin.user_id,
             "note": body.note},
        )
    record_audit(
        db, correlation_id=correlation_id(request), action="platform.admin.granted", entity_type="platform_admin",
        entity_id=str(admin_id), actor_subject=admin.subject,
        reason=body.note, after={"email": email, "user_id": str(user["id"])},
    )
    return {"id": str(admin_id), "user_id": str(user["id"]), "email": email,
            "status": "active"}


class AdminRevoke(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@platform_router.post("/admins/{user_id}/revoke")
def revoke_admin(
    request: Request,
    user_id: uuid.UUID,
    body: AdminRevoke,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("staff.manage")),
):
    """Take the level away. Never from yourself, and never from the last one.

    Both refusals are about the same failure: a platform with nobody able to
    administer it cannot grant anyone the ability, because granting it is
    itself a platform action. The only way back would be database access.
    """
    if user_id == admin.user_id:
        raise HTTPException(
            status_code=409,
            detail="You cannot revoke your own platform access. Ask another "
                   "platform administrator to do it.")
    row = _one(
        db,
        "SELECT id, status FROM iam.platform_admins WHERE user_id = :u",
        {"u": user_id},
    )
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=404, detail="Not platform staff")
    remaining = db.execute(
        text("SELECT count(*) FROM iam.platform_admins "
             "WHERE status = 'active' AND user_id <> :u"),
        {"u": user_id},
    ).scalar()
    if not remaining:
        raise HTTPException(
            status_code=409,
            detail="This is the only platform administrator left.")
    db.execute(
        text("UPDATE iam.platform_admins SET status = 'revoked', "
             "revoked_at = now(), updated_at = now() WHERE id = :id"),
        {"id": row["id"]},
    )
    record_audit(
        db, correlation_id=correlation_id(request), action="platform.admin.revoked", entity_type="platform_admin",
        entity_id=str(row["id"]), actor_subject=admin.subject,
        reason=body.reason, before={"status": "active"},
        after={"status": "revoked", "user_id": str(user_id)},
    )
    return {"user_id": str(user_id), "status": "revoked"}


# ============================================== platform roles (screen 15) ==

@platform_router.get("/capabilities")
def list_capabilities(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("staff.view")),
):
    """Every capability the console defines, and which roles grant it.

    The catalogue is seeded with the code rather than editable here, and that
    is deliberate: a capability exists because a route asks for it by name, so
    inventing one would create an authority nothing checks, and deleting one
    would silently open a route to everybody holding the role.
    """
    return _rows(
        db,
        """
        SELECT p.code, p.description,
               coalesce(array_agg(r.code ORDER BY r.code)
                        FILTER (WHERE r.code IS NOT NULL), '{}') AS granted_to
        FROM iam.platform_permissions p
        LEFT JOIN iam.platform_role_permissions rp ON rp.permission_id = p.id
        LEFT JOIN iam.platform_roles r ON r.id = rp.role_id
        GROUP BY p.code, p.description
        ORDER BY p.code
        """,
    )


@platform_router.get("/roles")
def list_roles(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("staff.view")),
):
    """The platform roles, what each grants, and how many people hold it."""
    return _rows(
        db,
        """
        SELECT r.id, r.code, r.name, r.description, r.is_system,
               coalesce(array_agg(DISTINCT p.code)
                        FILTER (WHERE p.code IS NOT NULL), '{}') AS capabilities,
               (SELECT count(*) FROM iam.platform_role_assignments ra
                 JOIN iam.platform_admins pa ON pa.id = ra.platform_admin_id
                WHERE ra.role_id = r.id AND pa.status = 'active') AS held_by
        FROM iam.platform_roles r
        LEFT JOIN iam.platform_role_permissions rp ON rp.role_id = r.id
        LEFT JOIN iam.platform_permissions p ON p.id = rp.permission_id
        GROUP BY r.id, r.code, r.name, r.description, r.is_system
        ORDER BY r.name
        """,
    )


class RoleCapabilitiesIn(BaseModel):
    capabilities: list[str] = Field(max_length=100)
    reason: str = Field(min_length=3, max_length=300)


@platform_router.put("/roles/{role_id}/capabilities")
def set_role_capabilities(
    request: Request,
    role_id: uuid.UUID,
    body: RoleCapabilitiesIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("staff.manage")),
):
    """Replace what a role grants.

    Two refusals worth stating. An unknown capability is rejected rather than
    ignored, because a typo that silently grants nothing produces a role that
    looks right in the console and denies in production. And the last role
    granting ``staff.manage`` cannot have it removed: that edit would leave
    the console unadministrable without database access.
    """
    role = _one(
        db,
        "SELECT id, code, name FROM iam.platform_roles WHERE id = :r",
        {"r": role_id},
    )
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    wanted = sorted(set(body.capabilities))
    known = {
        r["code"] for r in _rows(db, "SELECT code FROM iam.platform_permissions")
    }
    unknown = [c for c in wanted if c not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"No such capability: {', '.join(unknown)}")

    before = sorted(
        r["code"] for r in _rows(
            db,
            "SELECT p.code FROM iam.platform_role_permissions rp "
            "JOIN iam.platform_permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :r",
            {"r": role_id},
        )
    )

    if "staff.manage" in before and "staff.manage" not in wanted:
        still = db.execute(
            text(
                """
                SELECT count(*)
                FROM iam.platform_role_assignments ra
                JOIN iam.platform_admins pa ON pa.id = ra.platform_admin_id
                JOIN iam.platform_role_permissions rp ON rp.role_id = ra.role_id
                JOIN iam.platform_permissions p ON p.id = rp.permission_id
                WHERE pa.status = 'active' AND p.code = 'staff.manage'
                  AND ra.role_id <> :r
                """
            ),
            {"r": role_id},
        ).scalar()
        if not still:
            raise HTTPException(
                status_code=409,
                detail="This is the only role granting staff.manage. Removing "
                       "it would leave nobody able to administer the console.")

    db.execute(
        text("DELETE FROM iam.platform_role_permissions WHERE role_id = :r"),
        {"r": role_id},
    )
    if wanted:
        db.execute(
            text(
                "INSERT INTO iam.platform_role_permissions "
                "(role_id, permission_id) "
                "SELECT :r, p.id FROM iam.platform_permissions p "
                "WHERE p.code = ANY(:codes)"
            ),
            {"r": role_id, "codes": wanted},
        )
    db.execute(
        text("UPDATE iam.platform_roles SET updated_at = now() WHERE id = :r"),
        {"r": role_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.role.capabilities_changed",
        entity_type="platform_role", entity_id=str(role_id),
        actor_subject=admin.subject, reason=body.reason,
        before={"capabilities": before}, after={"capabilities": wanted},
    )
    return {"id": str(role_id), "code": role["code"], "capabilities": wanted}


class AdminRolesIn(BaseModel):
    roles: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=3, max_length=300)


@platform_router.put("/admins/{user_id}/roles")
def set_admin_roles(
    request: Request,
    user_id: uuid.UUID,
    body: AdminRolesIn,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("staff.manage")),
):
    """Set which roles a platform staff member holds.

    At least one role, because a platform administrator with none can sign in
    and do nothing -- an account that looks active and fails every action is
    worse than one that was never granted.

    You may not change your own roles. Not because it is dangerous in itself,
    but because it is the one edit nobody else needs to approve, and a tier
    whose members can quietly widen themselves has no separation of duties --
    which is the entire reason these tables exist.
    """
    if user_id == admin.user_id:
        raise HTTPException(
            status_code=409,
            detail="You cannot change your own roles. Ask another "
                   "administrator who holds staff.manage.")

    row = _one(
        db,
        "SELECT pa.id, u.display_name FROM iam.platform_admins pa "
        "JOIN iam.users u ON u.id = pa.user_id "
        "WHERE pa.user_id = :u AND pa.status = 'active'",
        {"u": user_id},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Not platform staff")

    wanted = sorted(set(body.roles))
    found = _rows(
        db,
        "SELECT id, code FROM iam.platform_roles WHERE code = ANY(:codes)",
        {"codes": wanted},
    )
    unknown = sorted(set(wanted) - {r["code"] for r in found})
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"No such role: {', '.join(unknown)}")

    before = sorted(
        r["code"] for r in _rows(
            db,
            "SELECT r.code FROM iam.platform_role_assignments ra "
            "JOIN iam.platform_roles r ON r.id = ra.role_id "
            "WHERE ra.platform_admin_id = :a",
            {"a": row["id"]},
        )
    )
    db.execute(
        text("DELETE FROM iam.platform_role_assignments "
             "WHERE platform_admin_id = :a"),
        {"a": row["id"]},
    )
    for r in found:
        db.execute(
            text("INSERT INTO iam.platform_role_assignments "
                 "(platform_admin_id, role_id, granted_by) "
                 "VALUES (:a, :r, :by)"),
            {"a": row["id"], "r": r["id"], "by": admin.user_id},
        )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.admin.roles_changed", entity_type="platform_admin",
        entity_id=str(row["id"]), actor_subject=admin.subject,
        reason=body.reason,
        before={"roles": before},
        after={"roles": wanted, "user_id": str(user_id)},
    )
    return {"user_id": str(user_id), "roles": wanted}


@platform_router.get("/me")
def whoami(
    admin: PlatformCaller = Depends(require_capability("staff.view")),
):
    """What this operator may do, so the console can hide what they cannot.

    Hiding is a courtesy, not the control: every route checks its own
    capability server-side, so a button left visible by a stale cache still
    fails at the API.
    """
    return {
        "subject": admin.subject,
        "user_id": str(admin.user_id),
        "roles": sorted(admin.roles),
        "capabilities": sorted(admin.capabilities),
    }


# ============================== property overview + onboarding (09, 07) ====

@platform_router.get("/properties/{property_id}")
def property_overview(
    property_id: uuid.UUID,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("property.view")),
):
    """One property, its tenant, its readiness and its entitlements.

    Configuration and service state only. What a property's guests are called,
    what they were charged and what they owe are not on this screen and have
    no endpoint behind it -- the platform runs the platform.
    """
    p = _one(
        db,
        """
        SELECT p.id, p.code, p.name, p.status, p.timezone, p.currency,
               p.property_type, p.contact_email, p.contact_phone,
               p.address_line, p.city, p.state, p.postal_code, p.country,
               p.checkin_time, p.checkout_time, p.created_at,
               p.organization_id, o.name AS organization_name,
               o.code AS organization_code, o.status AS organization_status
        FROM iam.properties p
        JOIN iam.organizations o ON o.id = p.organization_id
        WHERE p.id = :p
        """,
        {"p": property_id},
    )
    if p is None:
        raise HTTPException(status_code=404, detail="Property not found")

    p["onboarding"] = _one(
        db,
        "SELECT current_step, steps, started_at, activated_at "
        "FROM iam.property_onboarding WHERE property_id = :p",
        {"p": property_id},
    )
    p["modules"] = _rows(
        db,
        "SELECT module_code, enabled, enabled_at FROM iam.property_modules "
        "WHERE property_id = :p ORDER BY module_code",
        {"p": property_id},
    )
    # The commercial half of the same question, kept beside the operational
    # one. `entitled` is what the tenant's plan grants; `modules` is what the
    # property has switched on. They are separate systems and nothing bridges
    # them, so a screen that showed only one would imply the other.
    p["entitlements"] = _rows(
        db,
        """
        SELECT e.code, e.source
        FROM billing.entitlements e
        JOIN billing.subscriptions s ON s.id = e.subscription_id
        WHERE s.organization_id = :o
          AND s.status IN ('trialing', 'active', 'past_due', 'grace')
          AND e.kind = 'module'
        ORDER BY e.code
        """,
        {"o": p["organization_id"]},
    )
    p["capacity"] = _one(
        db,
        """
        SELECT (SELECT count(*) FROM property.rooms r
                 WHERE r.property_id = :p) AS rooms,
               (SELECT count(*) FROM property.room_types t
                 WHERE t.property_id = :p) AS room_types
        """,
        {"p": property_id},
    )
    # Where its business date has got to -- the drift check, for one property.
    p["business_date"] = _one(
        db,
        """
        SELECT r.business_date AS last_audited_date, r.status AS last_run_status,
               r.completed_at,
               (r.business_date - (now() AT TIME ZONE :tz)::date) AS days_ahead
        FROM finance.night_audit_runs r
        WHERE r.property_id = :p
        ORDER BY r.business_date DESC, r.run_number DESC LIMIT 1
        """,
        {"p": property_id, "tz": p["timezone"] or "Asia/Kolkata"},
    )
    # Connected services, for the panel of the same name. Real state or
    # nothing: "Not configured" is a useful answer and an invented one is not.
    p["services"] = _one(
        db,
        """
        SELECT l.provider AS channel_provider,
               l.last_provision_status, l.last_push_status,
               (SELECT count(*) FROM distribution.channel_room_mappings m
                 WHERE m.link_id = l.id) AS room_mappings,
               (SELECT count(*) FROM distribution.channel_rate_mappings m
                 WHERE m.link_id = l.id) AS rate_mappings,
               (SELECT count(*) FROM distribution.channel_connections c
                 WHERE c.property_id = :p) AS ota_connections,
               (SELECT count(*) FROM distribution.channel_booking_events e
                 WHERE e.property_id = :p) AS bookings_received
        FROM distribution.channel_manager_links l
        WHERE l.property_id = :p
        """,
        {"p": property_id},
    )
    # Whether the tenant that owns this property is on a plan at all. Billing
    # is a tenant fact, shown here because a property cannot be sold on a
    # tenant with no subscription.
    p["billing"] = _one(
        db,
        """
        SELECT pl.name AS plan_name, s.status
        FROM billing.subscriptions s
        JOIN billing.plan_versions v ON v.id = s.plan_version_id
        JOIN billing.plans pl ON pl.id = v.plan_id
        WHERE s.organization_id = :o
          AND s.status IN ('trialing', 'active', 'past_due', 'grace')
        LIMIT 1
        """,
        {"o": p["organization_id"]},
    )
    p["recent_activity"] = _rows(
        db,
        """
        SELECT occurred_at, actor_subject, action, entity_type, reason
        FROM iam.audit_events
        WHERE property_id = :p
        ORDER BY occurred_at DESC LIMIT 15
        """,
        {"p": property_id},
    )
    return p


#: The wizard's steps, in the order it walks them. Named here because the
#: jsonb only records which have been visited and says nothing about order or
#: about which are required to go live.
ONBOARDING_STEPS = [
    ("property", "Property details", True),
    ("structure", "Buildings and floors", True),
    ("rooms", "Rooms and room types", True),
    ("rates", "Rates and plans", True),
    ("billing", "Invoice and tax setup", True),
    ("team", "Team and roles", False),
    ("import", "Import existing data", False),
    ("connections", "Channel connections", False),
    ("golive", "Go live", True),
]


@platform_router.get("/onboarding")
def onboarding_progress(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("tenant.view")),
):
    """Who is still setting up, how far they have got, and what is missing.

    A queue rather than a report: the useful ordering is the one that puts the
    property nobody has touched for longest at the top, because that is the
    tenant about to churn before they ever went live.

    A step is "visited" in the wizard's own record, which is not the same as
    done -- somebody can open a page and leave it empty. The counts below say
    visited, and the screen says so rather than implying completion.
    """
    rows = _rows(
        db,
        """
        SELECT p.id AS property_id, p.code, p.name, p.status,
               p.organization_id, o.name AS organization_name,
               ob.current_step, ob.steps, ob.started_at, ob.activated_at,
               ob.updated_at,
               (SELECT count(*) FROM property.rooms r
                 WHERE r.property_id = p.id) AS rooms,
               (SELECT max(a.occurred_at) FROM iam.audit_events a
                 WHERE a.property_id = p.id) AS last_activity_at,
               s.trial_ends_on,
               -- Whether this property is actually bookable by a guest.
               --
               -- Onboarding can reach 'golive' with every required step
               -- ticked and the property still invisible to the public,
               -- because nothing in the wizard, in tenant creation or in
               -- billing ever switches the booking engine on. That was
               -- invisible here: the queue said activated and the hotel was
               -- not on sale, and nobody could see the difference.
               CASE WHEN bm.property_id IS NULL THEN 'not_configured'
                    WHEN bm.enabled THEN 'enabled'
                    ELSE 'disabled' END AS booking_engine,
               -- What the plan says they may have, which today is nothing:
               -- no plan version lists booking_engine at all. Reported so
               -- the gap is visible rather than inferred.
               EXISTS (SELECT 1 FROM billing.entitlements e
                        WHERE e.subscription_id = s.id
                          AND e.kind = 'module'
                          AND e.code = 'booking_engine') AS booking_entitled
        FROM iam.properties p
        JOIN iam.organizations o ON o.id = p.organization_id
        LEFT JOIN iam.property_onboarding ob ON ob.property_id = p.id
        LEFT JOIN billing.subscriptions s
               ON s.organization_id = p.organization_id
              AND s.status IN ('trialing', 'active', 'past_due', 'grace')
        LEFT JOIN iam.property_modules bm
               ON bm.property_id = p.id
              AND bm.module_code = 'booking_engine'
        ORDER BY ob.activated_at NULLS FIRST, ob.updated_at
        """,
    )
    out = []
    for r in rows:
        visited = set((r.get("steps") or {}).keys())
        required = [c for c, _, req in ONBOARDING_STEPS if req]
        out.append({
            **{k: r[k] for k in
               ("property_id", "code", "name", "status", "organization_id",
                "organization_name", "current_step", "started_at",
                "activated_at", "rooms", "last_activity_at", "trial_ends_on",
                # Whether a guest can book this, and whether the plan
                # says they may. Named explicitly because this handler
                # builds its rows rather than passing the query
                # through -- a new column is invisible until listed.
                "booking_engine", "booking_entitled")},
            "live": r["activated_at"] is not None,
            "steps": [
                {"code": c, "label": label, "required": req,
                 "visited": c in visited}
                for c, label, req in ONBOARDING_STEPS
            ],
            "visited_count": len(visited & {c for c, _, _ in ONBOARDING_STEPS}),
            "total_steps": len(ONBOARDING_STEPS),
            "required_outstanding": [c for c in required if c not in visited],
        })
    return out


# =========================================== channel health (screen 17) ====

@platform_router.get("/channels")
def channel_health(
    provider: str | None = None,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("operations.view")),
):
    """Every property's channel connection, and whether it is actually working.

    The platform's own provider credentials are deliberately NOT here. This
    reads connection *state* -- what was last pushed, what was mapped, what
    arrived -- which is operational. Reading the secret that authenticates the
    connection is a different privilege and has no route.

    ``provision_status`` and ``push_status`` are separate on purpose. A link
    can be provisioned and never pushed, or push happily against a mapping
    that was only half created; collapsing them into one "healthy" would hide
    exactly the case worth finding.
    """
    return _rows(
        db,
        """
        SELECT l.id, l.provider, l.external_property_id,
               l.property_id, p.code AS property_code, p.name AS property_name,
               l.organization_id, o.code AS tenant_code, o.name AS tenant_name,
               l.last_push_status, l.last_push_detail, l.last_pushed_at,
               l.last_provision_status, l.last_provision_detail,
               l.last_provisioned_at,
               l.send_availability, l.send_rates, l.send_restrictions,
               l.notify_on_failure, l.currency,
               (SELECT count(*) FROM distribution.channel_room_mappings m
                 WHERE m.link_id = l.id) AS room_mappings,
               (SELECT count(*) FROM distribution.channel_rate_mappings m
                 WHERE m.link_id = l.id) AS rate_mappings,
               (SELECT count(*) FROM distribution.channel_connections c
                 WHERE c.property_id = l.property_id) AS ota_connections,
               (SELECT count(*) FROM distribution.channel_booking_events e
                 WHERE e.property_id = l.property_id) AS bookings_received,
               (SELECT max(e.received_at) FROM distribution.channel_booking_events e
                 WHERE e.property_id = l.property_id) AS last_booking_at,
               -- One word for the row, derived rather than stored: a link is
               -- only "ok" when it has provisioned, pushed and has something
               -- mapped to push. Anything else is named by what is wrong.
               CASE
                 WHEN l.last_provision_status IS DISTINCT FROM 'ok'
                      THEN 'provisioning'
                 WHEN l.last_push_status IS DISTINCT FROM 'ok' THEN 'push failed'
                 WHEN (SELECT count(*) FROM distribution.channel_room_mappings m
                        WHERE m.link_id = l.id) = 0 THEN 'unmapped'
                 ELSE 'ok'
               END AS health
        FROM distribution.channel_manager_links l
        JOIN iam.organizations o ON o.id = l.organization_id
        JOIN iam.properties p ON p.id = l.property_id
        WHERE (CAST(:prov AS text) IS NULL OR l.provider = :prov)
        ORDER BY o.name, p.name
        """,
        {"prov": provider or None},
    )


# ======================================== system health & jobs (screen 24) ==

@platform_router.get("/operations")
def system_operations(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("operations.view")),
):
    """Queues, scheduled jobs and webhook reconciliation, across every tenant.

    Three things that fail quietly and are invisible until somebody goes
    looking:

    * the **outbox** drains by a publisher; if nothing is consuming it the
      rows simply accumulate and every downstream integration silently stops;
    * a **night audit** day that never closed leaves that day's revenue
      unposted -- which is not the same as a failed run, because a run that
      was deliberately reversed and re-run leaves the day perfectly closed;
    * a payment webhook that arrives and matches no payment is money the
      provider believes it took and this system has not recorded.

    Counts and the oldest offender for each, because the useful question is
    "how long has this been broken", not "how many".
    """
    outbox = _one(
        db,
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE published_at IS NULL) AS pending,
               min(occurred_at) FILTER (WHERE published_at IS NULL)
                   AS oldest_pending,
               max(published_at) AS last_published
        FROM integration.outbox_events
        """,
        {},
    )
    by_type = _rows(
        db,
        """
        SELECT event_type, count(*) AS pending,
               min(occurred_at) AS oldest
        FROM integration.outbox_events
        WHERE published_at IS NULL
        GROUP BY event_type ORDER BY count(*) DESC LIMIT 10
        """,
    )
    audits = _rows(
        db,
        """
        SELECT r.status, count(*) AS runs, max(r.completed_at) AS latest
        FROM finance.night_audit_runs r
        GROUP BY r.status ORDER BY count(*) DESC
        """,
    )
    # A failed run is not the same thing as an unclosed day, and conflating
    # them raises an alarm on a healthy property. A day is only stuck when it
    # has been attempted and *no* attempt completed: a run that was
    # deliberately reversed and re-run leaves a completed run behind it, and
    # the day is closed.
    # Read from business_days, not from night_audit_runs.
    #
    # The previous query started FROM night_audit_runs, so it could only ever
    # see a day somebody had *attempted* and failed to close. A day nobody
    # tried at all has no run row, and was therefore invisible -- which is
    # exactly the shape of the failure that happened: the scheduler stopped
    # sweeping, three properties sat a day behind, and this screen reported
    # "every attempted day closed" the whole time. Technically true, and the
    # most misleading possible reading.
    #
    # business_days is the authoritative record of what is open, so a day is
    # now counted because it is open, not because an attempt is on file.
    # `attempts` is kept -- a day open after three failures is a different
    # problem from a day nobody has touched, and the screen shows both.
    unclosed = _rows(
        db,
        """
        SELECT p.id AS property_id, p.code, p.name AS property_name,
               o.name AS tenant_name, b.business_date,
               (CURRENT_DATE - b.business_date) AS days_open,
               (SELECT count(*) FROM finance.night_audit_runs r
                 WHERE r.property_id = b.property_id
                   AND r.business_date = b.business_date) AS attempts
        FROM finance.business_days b
        JOIN iam.properties p ON p.id = b.property_id
        JOIN iam.organizations o ON o.id = p.organization_id
        -- A property's own audit moment, mirroring the scheduler's rule: the
        -- day in progress is never due, and yesterday is not due until this
        -- property's local hour has passed. Without this the screen alarms on
        -- a property whose audit is simply later tonight -- one chose 23:00,
        -- so at 09:00 its previous day is legitimately still open. A health
        -- screen that cries wolf about a healthy property is the failure this
        -- console has already had once.
        CROSS JOIN LATERAL (
            SELECT (now() AT TIME ZONE COALESCE(p.timezone, 'Asia/Kolkata'))
                     AS local_now,
                   COALESCE(
                     (SELECT s.audit_hour FROM finance.night_audit_settings s
                       WHERE s.property_id = p.id),
                     -- NIGHT_AUDIT_HOUR's default in the finance service. The
                     -- two are kept in step by hand; they disagree only if
                     -- somebody moves the deployment default without moving
                     -- this, and the effect is a screen an hour out, not a
                     -- wrong close.
                     3) AS audit_hour
        ) m
        WHERE b.status <> 'closed'
          AND b.business_date <= (
                m.local_now::date
                - CASE WHEN EXTRACT(hour FROM m.local_now) >= m.audit_hour
                       THEN 1 ELSE 2 END)
        ORDER BY b.business_date, p.code
        """,
    )
    # Runs that were unwound on purpose. The step records why, and the reason
    # is worth showing -- "closed a date before it had occurred" is a
    # different story from a crash, and reads as one.
    reversed_runs = _rows(
        db,
        """
        SELECT r.id, p.name AS property_name, r.business_date,
               s.step_code, s.error AS reason, s.updated_at
        FROM finance.night_audit_steps s
        JOIN finance.night_audit_runs r ON r.id = s.run_id
        JOIN iam.properties p ON p.id = r.property_id
        WHERE s.step_code LIKE 'reversed%'
        ORDER BY s.updated_at DESC LIMIT 10
        """,
    )
    failed_steps = _rows(
        db,
        """
        SELECT s.step_code, s.status, count(*) AS occurrences,
               max(s.updated_at) AS last_seen,
               (array_agg(s.error ORDER BY s.updated_at DESC))[1] AS last_error
        FROM finance.night_audit_steps s
        WHERE s.status <> 'completed'
        GROUP BY s.step_code, s.status ORDER BY count(*) DESC LIMIT 10
        """,
    )
    webhooks = _rows(
        db,
        """
        SELECT provider, event_type, outcome, count(*) AS events,
               max(received_at) AS last_received
        FROM finance.provider_events
        GROUP BY provider, event_type, outcome
        ORDER BY count(*) DESC LIMIT 15
        """,
    )
    return {
        "outbox": outbox,
        "outbox_by_type": by_type,
        "night_audit": audits,
        "unclosed_days": unclosed,
        "reversed_runs": reversed_runs,
        "failed_steps": failed_steps,
        "webhooks": webhooks,
    }
