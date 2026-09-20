"""The platform's own operations: providers, domains, messaging, support,
recovery, settings and analytics (screens 16, 18-21, 25-27).

Split from ``platform_routes`` because that file had reached 1,700 lines and
these are a different subject: ``platform_routes`` administers *tenants*, and
this administers the *platform*. The gate is the same one -- every handler
names a capability and ``require_capability`` reads only the platform tables.

Three rules this module keeps, and each exists because the obvious shortcut is
wrong:

**A sealed secret goes in and never comes back.** ``POST`` accepts a
credential and stores Fernet ciphertext; no route returns it, and no route
returns a decrypted value "for convenience". What a screen gets is a masked
hint and when it was last rotated. An operator who needs the real key gets it
from the provider, not from us.

**Approving is not entering.** A support access grant records a request, a
tenant owner's approval and an expiry. Nothing in this system acts on an
approved grant, because a mechanism that opens somebody's guest data needs
designing with them in the room. The record is the honest half to build now,
and the console says so on the screen rather than implying a door exists.

**Nobody approves their own.** Restore requests and access grants both check
requester != approver in SQL, in the route, and in the database constraint.
Twice in code so the message is decent, once in the schema so it is true.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import date, datetime, timezone

from chirala_common.secretbox import SecretsNotConfigured, seal
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .audit import record_audit
from .database import get_session
from .platform_authz import (
    PlatformCaller,
    correlation_id,
    require_capability,
)
from .settings import settings

ops_router = APIRouter(
    prefix="/platform",
    tags=["platform-operations"],
    route_class=TransactionalRoute,
)

#: Providers this deployment integrates with, and what each one's secret is.
#: Listed rather than free-typed so the screen can offer the right fields and
#: so a typo does not quietly create a second, never-read row.
PROVIDERS = {
    "razorpay": {"name": "Razorpay", "kind": "payments",
                 "secret_label": "Key secret",
                 "fields": ["key_id", "webhook_path"]},
    "smtp": {"name": "SMTP relay", "kind": "messaging",
             "secret_label": "Password",
             "fields": ["host", "port", "username", "sender"]},
    "channex": {"name": "Channex", "kind": "channel-manager",
                "secret_label": "API key",
                "fields": ["base_url", "group_id"]},
    "s3": {"name": "Object storage", "kind": "storage",
           "secret_label": "Secret access key",
           "fields": ["endpoint", "bucket", "access_key_id"]},
}


def _rows(db: Session, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params or {}).mappings()]


def _one(db: Session, sql: str, params: dict | None = None) -> dict | None:
    r = db.execute(text(sql), params or {}).mappings().first()
    return dict(r) if r else None


def _org_or_404(db: Session, org_id: uuid.UUID) -> dict:
    org = _one(db, "SELECT id, code, name FROM iam.organizations WHERE id = :o",
               {"o": org_id})
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org


def _now() -> datetime:
    return datetime.now(timezone.utc)


# =================================================== 16 provider setup =====

class ProviderIn(BaseModel):
    provider: str
    environment: str = Field(pattern="^(sandbox|production)$")
    label: str = Field(min_length=1, max_length=120)
    config: dict = Field(default_factory=dict)
    #: Write-only. Omit it on an edit to leave the stored secret alone --
    #: there is no way to read one back, so a screen cannot round-trip it.
    secret: str | None = None


@ops_router.get("/providers")
def list_providers(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("provider.manage")),
):
    """Every provider connection, with a hint of the secret and never the
    secret.

    ``secret_hint`` is the last four characters of the *ciphertext*, not the
    plaintext -- enough to tell two rows apart after a rotation, and useless
    to anybody who obtains it. Showing the last four of a real API key would
    leak the one part of it people paste into support tickets.
    """
    configured = _rows(
        db,
        """
        SELECT id, provider, environment, label, config, status,
               last_verified_at, last_verify_detail, rotated_at, updated_at,
               (secret IS NOT NULL) AS has_secret,
               CASE WHEN secret IS NULL THEN NULL
                    ELSE right(secret, 4) END AS secret_hint
        FROM platform.provider_credentials
        ORDER BY provider, environment
        """,
    )
    have = {(r["provider"], r["environment"]) for r in configured}
    # Every provider the deployment knows about, including the ones nobody has
    # set up. A screen that lists only what exists cannot show what is
    # missing, and a missing production credential is the interesting case.
    catalogue = [
        {"provider": code, "name": meta["name"], "kind": meta["kind"],
         "secret_label": meta["secret_label"], "fields": meta["fields"],
         "environments": [
             {"environment": env, "configured": (code, env) in have}
             for env in ("sandbox", "production")]}
        for code, meta in PROVIDERS.items()
    ]
    return {
        "catalogue": catalogue,
        "connections": configured,
        # What the running services actually use today. A row in this table is
        # not yet wired to anything: the services still read their own
        # environment, and saying otherwise on a screen would be a lie an
        # operator acts on.
        # The platform's own gateway (this table) is not the tenants'
        # gateways (finance.payment_credentials, one per organisation, used
        # to take money from *their* guests). Conflating the two is how a
        # platform subscription charge ends up on a hotel's merchant account.
        "tenant_gateways": _rows(
            db,
            """
            SELECT c.provider, c.enabled, c.updated_at,
                   o.name AS tenant_name, o.code AS tenant_code,
                   (c.key_secret_sealed IS NOT NULL) AS has_key,
                   (c.webhook_secret_sealed IS NOT NULL) AS has_webhook_secret
            FROM finance.payment_credentials c
            JOIN iam.organizations o ON o.id = c.organization_id
            ORDER BY o.name
            """,
        ),
        "runtime": {
            "mail_configured": settings.mail_config.configured,
            "encryption_configured": bool(settings.credential_encryption_keys),
            "note": "Services read their live provider settings from the "
                    "environment. Rows here are the inventory of record, not "
                    "yet the source the runtime reads.",
        },
    }


@ops_router.post("/providers", status_code=201)
def upsert_provider(
    body: ProviderIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("provider.manage")),
):
    """Create or update one provider connection for one environment."""
    if body.provider not in PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider. Expected one of: "
                   f"{', '.join(sorted(PROVIDERS))}.")

    sealed = None
    if body.secret is not None:
        if not body.secret.strip():
            raise HTTPException(status_code=400,
                                detail="A secret cannot be blank. Omit the "
                                       "field to keep the stored one.")
        try:
            sealed = seal(settings.credential_encryption_keys,
                          body.secret.strip())
        except SecretsNotConfigured:
            raise HTTPException(
                status_code=503,
                detail="No encryption key is configured, so a credential "
                       "cannot be stored. Set CREDENTIAL_ENCRYPTION_KEYS.",
            ) from None

    existing = _one(
        db,
        "SELECT id, label, status, (secret IS NOT NULL) AS had_secret "
        "FROM platform.provider_credentials "
        "WHERE provider = :p AND environment = :e",
        {"p": body.provider, "e": body.environment},
    )

    row = _one(
        db,
        """
        INSERT INTO platform.provider_credentials
            (provider, environment, label, config, secret, status)
        -- Every use of :s is cast. A bare parameter compared with IS NULL
        -- gives Postgres nothing to infer a type from, and it refuses the
        -- whole statement rather than guessing.
        VALUES (:p, :e, :l, CAST(:cfg AS jsonb), CAST(:s AS text),
                CASE WHEN CAST(:s AS text) IS NULL
                     THEN 'unverified' ELSE 'configured' END)
        ON CONFLICT (provider, environment) DO UPDATE SET
            label = EXCLUDED.label,
            config = EXCLUDED.config,
            -- A null secret means "leave it": the screen cannot read the
            -- stored one back, so it cannot send it back either.
            secret = COALESCE(EXCLUDED.secret,
                              platform.provider_credentials.secret),
            rotated_at = CASE WHEN EXCLUDED.secret IS NOT NULL
                              THEN now()
                              ELSE platform.provider_credentials.rotated_at END,
            status = CASE WHEN EXCLUDED.secret IS NOT NULL THEN 'configured'
                          ELSE platform.provider_credentials.status END,
            updated_at = now()
        RETURNING id, provider, environment, label, status, rotated_at
        """,
        {"p": body.provider, "e": body.environment, "l": body.label.strip(),
         "cfg": json.dumps(body.config), "s": sealed},
    )

    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.provider.saved" if existing
               else "platform.provider.created",
        entity_type="provider_credential", entity_id=str(row["id"]),
        actor_subject=admin.subject,
        before={"label": existing["label"]} if existing else None,
        # The secret is never in the audit trail either, only the fact of it.
        after={"provider": body.provider, "environment": body.environment,
               "label": body.label.strip(),
               "secret_rotated": sealed is not None},
    )
    return row


@ops_router.post("/providers/{connection_id}/verify")
def verify_provider(
    connection_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("provider.manage")),
):
    """Record what can actually be checked about a connection.

    Deliberately *not* a live call to the provider. A button that says
    "verify" and reaches a payment gateway from an admin console is a button
    that can be used to probe one; and a check that can only confirm the
    row is well-formed should say exactly that rather than flashing a green
    tick that means nothing.
    """
    row = _one(
        db,
        "SELECT id, provider, environment, config, (secret IS NOT NULL) "
        "AS has_secret FROM platform.provider_credentials WHERE id = :i",
        {"i": connection_id},
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")

    expected = PROVIDERS.get(row["provider"], {}).get("fields", [])
    missing = [f for f in expected if not (row["config"] or {}).get(f)]
    if not row["has_secret"]:
        status_v, detail = "unverified", "No secret has been stored."
    elif missing:
        status_v = "unverified"
        detail = f"Stored, but incomplete: missing {', '.join(missing)}."
    else:
        status_v = "configured"
        detail = ("Complete and sealed. This check reads the stored record "
                  "only; it does not call the provider.")

    db.execute(
        text("UPDATE platform.provider_credentials SET status = :s, "
             "last_verified_at = now(), last_verify_detail = :d, "
             "updated_at = now() WHERE id = :i"),
        {"s": status_v, "d": detail, "i": connection_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.provider.verified",
        entity_type="provider_credential", entity_id=str(connection_id),
        actor_subject=admin.subject,
        after={"status": status_v, "detail": detail},
    )
    return {"status": status_v, "detail": detail, "missing": missing}


# ============================================ 18 booking engine domains ====

class DomainIn(BaseModel):
    property_id: uuid.UUID
    hostname: str = Field(min_length=4, max_length=253)


def _clean_host(raw: str) -> str:
    host = raw.strip().lower().rstrip(".")
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    host = host.split("/")[0]
    if "." not in host or " " in host or host.startswith("-"):
        raise HTTPException(
            status_code=400,
            detail="That is not a hostname. Give the name on its own, like "
                   "book.example.com.")
    return host


@ops_router.get("/domains")
def list_domains(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("domain.manage")),
):
    return {
        "domains": _rows(
            db,
            """
            SELECT d.id, d.hostname, d.status, d.verification_method,
                   d.verification_token, d.verified_at, d.tls_status,
                   d.tls_expires_at, d.created_at,
                   p.id AS property_id, p.code AS property_code,
                   p.name AS property_name,
                   o.name AS tenant_name, o.code AS tenant_code,
                   CASE WHEN d.tls_expires_at IS NULL THEN NULL
                        ELSE (d.tls_expires_at::date - CURRENT_DATE) END
                     AS tls_days_left
            FROM platform.booking_domains d
            JOIN iam.properties p ON p.id = d.property_id
            JOIN iam.organizations o ON o.id = d.organization_id
            ORDER BY d.created_at DESC
            """,
        ),
        # Every active property HAS a /book/{code} URL. Whether that URL takes
        # bookings is a different question, and this screen used to answer it
        # wrongly -- it listed all four and footnoted "these work today" while
        # three of them returned 404.
        #
        # The gate is the booking_engine entitlement, folded into the public
        # lookup in booking-core so a property is never silently on sale. So
        # the entitlement is reported here rather than assumed: an operator
        # reading this needs to know which links they can actually hand to a
        # hotel.
        "properties": _rows(
            db,
            """
            SELECT p.id, p.code, p.name, o.name AS tenant_name,
                   (SELECT count(*) FROM platform.booking_domains d
                     WHERE d.property_id = p.id
                       AND d.status <> 'retired') AS domains,
                   -- Three states, not two: never configured is not the same
                   -- as deliberately switched off, and an operator chasing a
                   -- dead link needs to know which one they are looking at.
                   CASE WHEN m.property_id IS NULL THEN 'not_configured'
                        WHEN m.enabled THEN 'enabled'
                        ELSE 'disabled' END AS booking_engine,
                   (SELECT count(*) FROM property.room_types rt
                     WHERE rt.property_id = p.id
                       AND rt.status = 'active') AS room_types
            FROM iam.properties p
            JOIN iam.organizations o ON o.id = p.organization_id
            LEFT JOIN iam.property_modules m
                   ON m.property_id = p.id
                  AND m.module_code = 'booking_engine'
            WHERE p.status = 'active'
            ORDER BY o.name, p.name
            """,
        ),
        "hosted_pattern": "/book/{property_code}",
    }


@ops_router.post("/domains", status_code=201)
def add_domain(
    body: DomainIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("domain.manage")),
):
    """Register a custom hostname, unverified, with a token to prove it.

    Nothing is served from the name until somebody proves they control it. A
    hostname pointed at this platform by a party who does not own it is a
    phishing page with a real booking engine behind it, and "we checked the
    DNS" is the only thing standing between those two outcomes.
    """
    host = _clean_host(body.hostname)
    prop = _one(
        db,
        "SELECT p.id, p.code, p.name, p.organization_id, o.name AS tenant_name "
        "FROM iam.properties p JOIN iam.organizations o "
        "ON o.id = p.organization_id WHERE p.id = :p",
        {"p": body.property_id},
    )
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")

    clash = _one(db, "SELECT id, property_id FROM platform.booking_domains "
                     "WHERE hostname = :h", {"h": host})
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"{host} is already registered"
                   + (" to this property." if clash["property_id"] == prop["id"]
                      else " to another property."))

    token = "chirala-verify=" + secrets.token_urlsafe(18)
    row = _one(
        db,
        """
        INSERT INTO platform.booking_domains
            (organization_id, property_id, hostname, verification_token)
        VALUES (:o, :p, :h, :t)
        RETURNING id, hostname, status, verification_token, verification_method
        """,
        {"o": prop["organization_id"], "p": prop["id"], "h": host, "t": token},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.domain.added", entity_type="booking_domain",
        entity_id=str(row["id"]), organization_id=prop["organization_id"],
        property_id=prop["id"], actor_subject=admin.subject,
        after={"hostname": host, "property_code": prop["code"]},
    )
    return {**row, "property_code": prop["code"],
            "tenant_name": prop["tenant_name"],
            "instruction": f"Add a TXT record at _chirala.{host} "
                           f"containing {token}"}


class DomainStateIn(BaseModel):
    action: str = Field(pattern="^(verify|activate|retire)$")


@ops_router.post("/domains/{domain_id}/state")
def set_domain_state(
    domain_id: uuid.UUID,
    body: DomainStateIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("domain.manage")),
):
    """Move a domain along its lifecycle, one legal step at a time.

    ``verify`` marks the DNS check as passed. It does not *perform* the check:
    this service does not resolve DNS, so an operator confirms it out of band
    and this records who said so. Pretending to have resolved a name we never
    queried would be the worse kind of green tick.
    """
    row = _one(
        db,
        "SELECT id, hostname, status, organization_id, property_id "
        "FROM platform.booking_domains WHERE id = :i", {"i": domain_id})
    if row is None:
        raise HTTPException(status_code=404, detail="Domain not found")

    if body.action == "verify":
        if row["status"] not in ("pending", "failed"):
            raise HTTPException(status_code=409,
                                detail=f"{row['hostname']} is already "
                                       f"{row['status']}.")
        sets = ("status = 'verified', verified_at = now(), "
                "tls_status = 'pending'")
    elif body.action == "activate":
        if row["status"] != "verified":
            raise HTTPException(
                status_code=409,
                detail="A domain has to be verified before it can go live.")
        # An issued certificate with a real expiry, so the expiry screen has
        # something true to count down. Ninety days is the Let's Encrypt term.
        sets = ("status = 'live', tls_status = 'issued', "
                "tls_expires_at = now() + interval '90 days'")
    else:
        sets = "status = 'retired', tls_status = 'none'"

    db.execute(text(f"UPDATE platform.booking_domains SET {sets}, "
                    "updated_at = now() WHERE id = :i"), {"i": domain_id})
    record_audit(
        db, correlation_id=correlation_id(request),
        action=f"platform.domain.{body.action}", entity_type="booking_domain",
        entity_id=str(domain_id), organization_id=row["organization_id"],
        property_id=row["property_id"], actor_subject=admin.subject,
        before={"status": row["status"]}, after={"action": body.action},
    )
    return _one(db, "SELECT id, hostname, status, tls_status, tls_expires_at "
                    "FROM platform.booking_domains WHERE id = :i",
                {"i": domain_id})


# ======================================================== 19 messaging ====

@ops_router.get("/messaging")
def messaging(
    days: int = 14,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("messaging.manage")),
):
    """Templates, and what was actually sent with them."""
    window = max(1, min(days, 90))
    templates = _rows(
        db,
        """
        SELECT t.id, t.code, t.version, t.name, t.channel, t.subject,
               t.status, t.variables, t.updated_at,
               length(t.body_text) AS body_length,
               (t.body_html IS NOT NULL) AS has_html,
               (SELECT count(*) FROM platform.message_deliveries d
                 WHERE d.template_code = t.code
                   AND d.sent_at > now() - make_interval(days => :w)) AS sent
        FROM platform.message_templates t
        WHERE t.status <> 'retired'
        ORDER BY t.code, t.version DESC
        """,
        {"w": window},
    )
    deliveries = _rows(
        db,
        """
        SELECT d.id, d.template_code, d.channel, d.recipient, d.subject,
               d.status, d.detail, d.attempts, d.sent_at, o.name AS tenant_name
        FROM platform.message_deliveries d
        LEFT JOIN iam.organizations o ON o.id = d.organization_id
        ORDER BY d.sent_at DESC LIMIT 100
        """,
    )
    totals = _one(
        db,
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status = 'sent') AS sent,
               count(*) FILTER (WHERE status <> 'sent') AS failed,
               max(sent_at) AS last_sent
        FROM platform.message_deliveries
        WHERE sent_at > now() - make_interval(days => :w)
        """,
        {"w": window},
    )
    return {"templates": templates, "deliveries": deliveries,
            "totals": totals, "window_days": window,
            "mail_configured": settings.mail_config.configured}


class TemplateIn(BaseModel):
    name: str | None = None
    subject: str | None = None
    body_text: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|published)$")


@ops_router.put("/messaging/templates/{template_id}")
def update_template(
    template_id: uuid.UUID,
    body: TemplateIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("messaging.manage")),
):
    """Edit the wording of a template, and nothing structural.

    The variables a template may use are fixed by the code that renders it,
    so they are not editable here: a screen that let somebody add
    ``{{refund_amount}}`` to the welcome mail would produce an email with a
    literal ``{{refund_amount}}`` in it and no error anywhere.
    """
    row = _one(db, "SELECT id, code, version, name, subject, status, variables "
                   "FROM platform.message_templates WHERE id = :i",
               {"i": template_id})
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")

    if body.body_text is not None:
        known = set(row["variables"] or [])
        used = set(re.findall(r"\{\{\s*([a-z_]+)\s*\}\}", body.body_text))
        unknown = sorted(used - known)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"This template does not receive {', '.join(unknown)}. "
                       f"It has: {', '.join(sorted(known)) or 'no variables'}.")

    db.execute(
        text("""
            UPDATE platform.message_templates SET
                name = COALESCE(:n, name),
                subject = COALESCE(:s, subject),
                body_text = COALESCE(:b, body_text),
                status = COALESCE(:st, status),
                updated_at = now()
            WHERE id = :i
        """),
        {"n": body.name, "s": body.subject, "b": body.body_text,
         "st": body.status, "i": template_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.template.updated", entity_type="message_template",
        entity_id=str(template_id), actor_subject=admin.subject,
        before={"name": row["name"], "subject": row["subject"],
                "status": row["status"]},
        after={k: v for k, v in body.model_dump().items() if v is not None},
    )
    return _one(db, "SELECT id, code, version, name, subject, status "
                    "FROM platform.message_templates WHERE id = :i",
                {"i": template_id})


# ================================================= 20/21 support inbox ====

#: How long a ticket of each priority may wait, in hours. First response and
#: resolution are counted separately because they fail separately: a customer
#: whose urgent ticket was acknowledged in ten minutes and fixed in two days
#: had a different experience from one who heard nothing for two days.
SLA = {"urgent": (1, 8), "high": (4, 24), "normal": (12, 72), "low": (24, 168)}


@ops_router.get("/support")
def support_inbox(
    status: str | None = None,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("support.view")),
):
    tickets = _rows(
        db,
        """
        SELECT t.id, t.reference, t.subject, t.priority, t.status,
               t.opened_by, t.created_at, t.updated_at, t.resolved_at,
               t.first_response_due, t.resolution_due,
               o.id AS organization_id, o.name AS tenant_name,
               o.code AS tenant_code,
               p.name AS property_name, p.code AS property_code,
               u.display_name AS assignee,
               (SELECT count(*) FROM platform.support_messages m
                 WHERE m.ticket_id = t.id) AS replies,
               (SELECT min(m.created_at) FROM platform.support_messages m
                 WHERE m.ticket_id = t.id AND m.from_side = 'platform')
                 AS first_response_at,
               -- Breached is a fact about time, computed here rather than in
               -- the browser, so every screen and every export agrees.
               (t.status IN ('open', 'waiting')
                AND t.resolution_due IS NOT NULL
                AND t.resolution_due < now()) AS breached
        FROM platform.support_tickets t
        JOIN iam.organizations o ON o.id = t.organization_id
        LEFT JOIN iam.properties p ON p.id = t.property_id
        LEFT JOIN iam.users u ON u.id = t.assigned_to
        WHERE (CAST(:st AS text) IS NULL OR t.status = CAST(:st AS text))
        ORDER BY
            CASE t.status WHEN 'open' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END,
            CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                            WHEN 'normal' THEN 2 ELSE 3 END,
            t.created_at
        LIMIT 200
        """,
        {"st": status},
    )
    agents = _rows(
        db,
        """
        SELECT u.id, u.display_name, u.email,
               (SELECT count(*) FROM platform.support_tickets t
                 WHERE t.assigned_to = u.id
                   AND t.status IN ('open', 'waiting')) AS open_tickets
        FROM iam.platform_admins a
        JOIN iam.users u ON u.id = a.user_id
        WHERE a.status = 'active'
        ORDER BY u.display_name
        """,
    )
    return {"tickets": tickets, "agents": agents,
            "sla": {k: {"first_response_hours": v[0], "resolution_hours": v[1]}
                    for k, v in SLA.items()}}


class TicketIn(BaseModel):
    organization_id: uuid.UUID
    property_id: uuid.UUID | None = None
    subject: str = Field(min_length=3, max_length=200)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    opened_by: str | None = None
    body: str = Field(min_length=1)


@ops_router.post("/support", status_code=201)
def open_ticket(
    body: TicketIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("support.manage")),
):
    org = _org_or_404(db, body.organization_id)
    first, resolve = SLA[body.priority]
    # A short, human reference. Tickets get quoted in email and read out on
    # the phone, and a uuid is neither.
    seq = _one(db, "SELECT count(*) + 1 AS n FROM platform.support_tickets")["n"]
    reference = f"CB-{date.today():%y%m}-{seq:04d}"

    ticket = _one(
        db,
        """
        INSERT INTO platform.support_tickets
            (reference, organization_id, property_id, subject, priority,
             opened_by, first_response_due, resolution_due)
        VALUES (:ref, :o, :p, :s, :pr, :by,
                now() + make_interval(hours => :fh),
                now() + make_interval(hours => :rh))
        RETURNING id, reference, subject, priority, status, created_at
        """,
        {"ref": reference, "o": org["id"], "p": body.property_id,
         "s": body.subject.strip(), "pr": body.priority,
         "by": (body.opened_by or "").strip() or None,
         "fh": first, "rh": resolve},
    )
    db.execute(
        text("INSERT INTO platform.support_messages "
             "(ticket_id, author, from_side, body) "
             "VALUES (:t, :a, 'tenant', :b)"),
        {"t": ticket["id"], "a": (body.opened_by or org["name"]),
         "b": body.body.strip()},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.ticket.opened", entity_type="support_ticket",
        entity_id=str(ticket["id"]), organization_id=org["id"],
        property_id=body.property_id, actor_subject=admin.subject,
        after={"reference": reference, "priority": body.priority,
               "subject": body.subject.strip()},
    )
    return {**ticket, "tenant_name": org["name"]}


@ops_router.get("/support/{ticket_id}")
def ticket_detail(
    ticket_id: uuid.UUID,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("support.view")),
):
    ticket = _one(
        db,
        """
        SELECT t.*, o.name AS tenant_name, o.code AS tenant_code,
               p.name AS property_name, p.code AS property_code,
               u.display_name AS assignee
        FROM platform.support_tickets t
        JOIN iam.organizations o ON o.id = t.organization_id
        LEFT JOIN iam.properties p ON p.id = t.property_id
        LEFT JOIN iam.users u ON u.id = t.assigned_to
        WHERE t.id = :i
        """,
        {"i": ticket_id},
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {
        "ticket": ticket,
        "messages": _rows(
            db,
            "SELECT id, author, from_side, body, created_at "
            "FROM platform.support_messages WHERE ticket_id = :i "
            "ORDER BY created_at",
            {"i": ticket_id},
        ),
        "grants": _rows(
            db,
            """
            SELECT g.id, g.reason, g.scope, g.minutes, g.status,
                   g.created_at, g.approved_at, g.expires_at, g.revoked_at,
                   r.display_name AS requested_by_name,
                   a.display_name AS approved_by_name,
                   (g.status = 'approved' AND g.expires_at > now())
                     AS currently_valid
            FROM platform.support_access_grants g
            JOIN iam.users r ON r.id = g.requested_by
            LEFT JOIN iam.users a ON a.id = g.approved_by
            WHERE g.ticket_id = :i
            ORDER BY g.created_at DESC
            """,
            {"i": ticket_id},
        ),
        "scopes": [
            "reservations.read", "folio.read", "configuration.read",
            "rates.read", "reports.read",
        ],
    }


class ReplyIn(BaseModel):
    body: str = Field(min_length=1)


@ops_router.post("/support/{ticket_id}/reply", status_code=201)
def reply_to_ticket(
    ticket_id: uuid.UUID,
    body: ReplyIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("support.manage")),
):
    t = _one(db, "SELECT id, organization_id, status FROM "
                 "platform.support_tickets WHERE id = :i", {"i": ticket_id})
    if t is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if t["status"] == "closed":
        raise HTTPException(status_code=409,
                            detail="This ticket is closed. Reopen it first.")
    db.execute(
        text("INSERT INTO platform.support_messages "
             "(ticket_id, author, from_side, body) "
             "VALUES (:t, :a, 'platform', :b)"),
        {"t": ticket_id, "a": admin.subject, "b": body.body.strip()},
    )
    db.execute(
        text("UPDATE platform.support_tickets SET status = 'waiting', "
             "updated_at = now() WHERE id = :i AND status = 'open'"),
        {"i": ticket_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.ticket.replied", entity_type="support_ticket",
        entity_id=str(ticket_id), organization_id=t["organization_id"],
        actor_subject=admin.subject,
    )
    return {"detail": "Reply added."}


class TicketUpdateIn(BaseModel):
    status: str | None = Field(default=None,
                               pattern="^(open|waiting|resolved|closed)$")
    priority: str | None = Field(default=None,
                                 pattern="^(low|normal|high|urgent)$")
    assigned_to: uuid.UUID | None = None


@ops_router.put("/support/{ticket_id}")
def update_ticket(
    ticket_id: uuid.UUID,
    body: TicketUpdateIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("support.manage")),
):
    t = _one(db, "SELECT id, organization_id, status, priority, assigned_to "
                 "FROM platform.support_tickets WHERE id = :i", {"i": ticket_id})
    if t is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if body.assigned_to is not None:
        ok = _one(db, "SELECT 1 AS y FROM iam.platform_admins "
                      "WHERE user_id = :u AND status = 'active'",
                  {"u": body.assigned_to})
        if not ok:
            raise HTTPException(
                status_code=400,
                detail="A ticket can only be assigned to active platform "
                       "staff.")
    db.execute(
        text("""
            UPDATE platform.support_tickets SET
                status = COALESCE(:st, status),
                priority = COALESCE(:pr, priority),
                assigned_to = COALESCE(:assignee, assigned_to),
                resolved_at = CASE WHEN :st IN ('resolved', 'closed')
                                   THEN COALESCE(resolved_at, now())
                                   WHEN :st IS NOT NULL THEN NULL
                                   ELSE resolved_at END,
                updated_at = now()
            WHERE id = :i
        """),
        {"st": body.status, "pr": body.priority,
         "assignee": body.assigned_to,
         "i": ticket_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.ticket.updated", entity_type="support_ticket",
        entity_id=str(ticket_id), organization_id=t["organization_id"],
        actor_subject=admin.subject,
        before={"status": t["status"], "priority": t["priority"]},
        after={k: str(v) for k, v in body.model_dump().items() if v is not None},
    )
    return {"detail": "Ticket updated."}


class AccessRequestIn(BaseModel):
    organization_id: uuid.UUID
    property_id: uuid.UUID | None = None
    reason: str = Field(min_length=10, max_length=400)
    scope: list[str] = Field(min_length=1)
    minutes: int = Field(default=15, ge=5, le=480)


@ops_router.post("/support/{ticket_id}/access", status_code=201)
def request_access(
    ticket_id: uuid.UUID,
    body: AccessRequestIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(
        require_capability("support.request_access")),
):
    """Ask a tenant for scoped, time-boxed access to their data.

    This creates a *request*. It does not create access, and approving it will
    not create access either: no code path in this system reads a grant and
    widens anybody's visibility. Building the request first is deliberate --
    the record of who asked, for what and why, is the part that has to exist
    before the door does, not after.
    """
    org = _org_or_404(db, body.organization_id)
    t = _one(db, "SELECT id, organization_id FROM platform.support_tickets "
                 "WHERE id = :i", {"i": ticket_id})
    if t is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if t["organization_id"] != body.organization_id:
        raise HTTPException(
            status_code=400,
            detail="That ticket belongs to a different tenant.")

    allowed = {"reservations.read", "folio.read", "configuration.read",
               "rates.read", "reports.read"}
    bad = sorted(set(body.scope) - allowed)
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Not a grantable scope: {', '.join(bad)}. Every scope is "
                   f"read-only by design.")

    row = _one(
        db,
        """
        INSERT INTO platform.support_access_grants
            (ticket_id, organization_id, property_id, requested_by, reason,
             scope, minutes)
        VALUES (:t, :o, :p, :u, :r, CAST(:sc AS text[]), :m)
        RETURNING id, status, reason, scope, minutes, created_at
        """,
        {"t": ticket_id, "o": org["id"], "p": body.property_id,
         "u": admin.user_id, "r": body.reason.strip(),
         "sc": "{" + ",".join(body.scope) + "}", "m": body.minutes},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.access.requested", entity_type="support_access_grant",
        entity_id=str(row["id"]), organization_id=org["id"],
        property_id=body.property_id, actor_subject=admin.subject,
        reason=body.reason.strip(),
        after={"scope": body.scope, "minutes": body.minutes},
    )
    return {**row, "tenant_name": org["name"],
            "note": "The tenant's owner has to approve this. Approval is "
                    "recorded; it does not by itself open any data."}


@ops_router.post("/support/access/{grant_id}/revoke")
def revoke_access(
    grant_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(
        require_capability("support.request_access")),
):
    """Withdraw a request or end a grant early.

    Available to platform staff because withdrawing your own request needs no
    ceremony. Granting is the direction that needs the tenant.
    """
    g = _one(db, "SELECT id, status, organization_id FROM "
                 "platform.support_access_grants WHERE id = :i", {"i": grant_id})
    if g is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if g["status"] in ("revoked", "expired", "refused"):
        raise HTTPException(status_code=409,
                            detail=f"This request is already {g['status']}.")
    db.execute(
        text("UPDATE platform.support_access_grants SET status = 'revoked', "
             "revoked_at = now() WHERE id = :i"), {"i": grant_id})
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.access.revoked", entity_type="support_access_grant",
        entity_id=str(grant_id), organization_id=g["organization_id"],
        actor_subject=admin.subject, before={"status": g["status"]},
    )
    return {"detail": "Withdrawn."}


# ============================================== 25 backups & recovery =====

@ops_router.get("/recovery")
def recovery(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("recovery.request")),
):
    """The backup inventory and the restore queue.

    ``verified_at`` is the column that matters. A backup nobody has restored
    is a hope, so the screen leads with how many have actually been proven and
    how old the newest proof is -- not with how many files exist.
    """
    snapshots = _rows(
        db,
        """
        SELECT s.id, s.label, s.scope, s.taken_at, s.size_bytes, s.location,
               s.verified_at, s.verify_detail, s.retain_until,
               -- A tenant export outlives its tenant, so the organisation
               -- join is null for exactly the rows that most need a name.
               -- The deletion record kept one.
               COALESCE(o.name, d.organization_name) AS tenant_name,
               d.organization_code AS deleted_tenant_code,
               d.completed_at      AS deleted_at,
               d.reason            AS deletion_reason,
               req.display_name    AS deletion_requested_by,
               app.display_name    AS deletion_approved_by,
               (CURRENT_DATE - s.taken_at::date) AS age_days
        FROM platform.backup_snapshots s
        LEFT JOIN iam.organizations o ON o.id = s.organization_id
        -- The export key is the link. A snapshot taken any other way simply
        -- has no matching row here.
        LEFT JOIN platform.tenant_deletions d
               ON d.export_location = s.location AND d.status = 'completed'
        LEFT JOIN iam.users req ON req.id = d.requested_by
        LEFT JOIN iam.users app ON app.id = d.approved_by
        ORDER BY s.taken_at DESC LIMIT 60
        """,
    )
    requests = _rows(
        db,
        """
        SELECT r.id, r.scope, r.reason, r.status, r.created_at,
               r.approved_at, r.verified_at, r.completed_at,
               s.label AS snapshot_label, s.taken_at,
               o.name AS tenant_name,
               req.display_name AS requested_by_name,
               app.display_name AS approved_by_name
        FROM platform.restore_requests r
        JOIN platform.backup_snapshots s ON s.id = r.snapshot_id
        LEFT JOIN iam.organizations o ON o.id = r.organization_id
        JOIN iam.users req ON req.id = r.requested_by
        LEFT JOIN iam.users app ON app.id = r.approved_by
        ORDER BY r.created_at DESC LIMIT 40
        """,
    )
    summary = _one(
        db,
        """
        SELECT count(*) AS snapshots,
               count(*) FILTER (WHERE verified_at IS NOT NULL) AS verified,
               max(taken_at) AS newest,
               max(verified_at) AS last_verified
        FROM platform.backup_snapshots
        """,
    )
    return {
        "snapshots": snapshots, "requests": requests, "summary": summary,
        "can_approve": "recovery.approve" in admin.capabilities,
        "note": "Restores are carried out by hand against the recorded "
                "snapshot. This queue records the decision and who made it; "
                "it does not execute anything.",
    }


class SnapshotIn(BaseModel):
    label: str = Field(min_length=3, max_length=150)
    scope: str = Field(default="platform", pattern="^(platform|tenant)$")
    organization_id: uuid.UUID | None = None
    taken_at: datetime | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    location: str | None = Field(default=None, max_length=400)
    retain_until: date | None = None


@ops_router.post("/recovery/snapshots", status_code=201)
def register_snapshot(
    body: SnapshotIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("recovery.request")),
):
    """Record that a backup exists.

    This service does not take backups -- Postgres and the infrastructure
    around it do -- so the register has to be written by whoever or whatever
    took one. Without this route the table could never hold a row, which made
    the whole screen an inventory of nothing and left "Request a restore"
    permanently disabled: a register with no way to register.

    Gated on ``recovery.request`` rather than a capability of its own. Both
    actions are the same job (operating the recovery module) with the same
    audience, and neither can approve anything -- a restore still needs a
    second person. A capability that only ever appears alongside another one
    is a longer list, not a tighter control.
    """
    if body.scope == "tenant" and body.organization_id is None:
        raise HTTPException(
            status_code=400,
            detail="A tenant snapshot has to name the tenant it covers.")
    if body.scope == "platform" and body.organization_id is not None:
        raise HTTPException(
            status_code=400,
            detail="A platform snapshot covers everything, so it cannot also "
                   "name one tenant. Record it as a tenant snapshot instead.")
    if body.organization_id is not None:
        _org_or_404(db, body.organization_id)

    taken = body.taken_at or _now()
    if taken > _now():
        raise HTTPException(
            status_code=400,
            detail="That snapshot is dated in the future. A backup is "
                   "recorded after it has been taken, not before.")

    row = _one(
        db,
        """
        INSERT INTO platform.backup_snapshots
            (label, scope, organization_id, taken_at, size_bytes, location,
             retain_until)
        VALUES (:l, :sc, :o, :t, :sz, :loc, :keep)
        RETURNING id, label, scope, taken_at, size_bytes, location,
                  retain_until, verified_at
        """,
        {"l": body.label.strip(), "sc": body.scope,
         "o": body.organization_id, "t": taken, "sz": body.size_bytes,
         "loc": (body.location or "").strip() or None,
         "keep": body.retain_until},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.backup.registered", entity_type="backup_snapshot",
        entity_id=str(row["id"]), organization_id=body.organization_id,
        actor_subject=admin.subject,
        after={"label": body.label.strip(), "scope": body.scope,
               "taken_at": taken.isoformat()},
    )
    return row


class SnapshotVerifyIn(BaseModel):
    #: What was actually checked. Required, and deliberately so -- see below.
    detail: str = Field(min_length=10, max_length=300)


@ops_router.post("/recovery/snapshots/{snapshot_id}/verify")
def verify_snapshot(
    snapshot_id: uuid.UUID,
    body: SnapshotVerifyIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("recovery.approve")),
):
    """Record that this backup was restored somewhere and came back intact.

    ``verified_at`` is the column the whole screen leads with, and until now
    nothing could set it -- so "proven by restore" counted zero however many
    backups existed, which is the most alarming possible reading of a healthy
    system and the least useful.

    ``detail`` is required rather than optional. "Verified" with no statement
    of what was checked is the tick that makes a register worthless: restoring
    one table and restoring the cluster are both "a restore", and six months
    later nobody remembers which one this was.

    Gated on ``recovery.approve`` rather than ``recovery.request``: this is an
    assurance other people act on -- somebody will choose this snapshot in an
    incident because it says verified -- so it belongs with the role that
    signs off restores rather than the one that asks for them.
    """
    snap = _one(db, "SELECT id, label, verified_at FROM "
                    "platform.backup_snapshots WHERE id = :i",
                {"i": snapshot_id})
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    db.execute(
        text("UPDATE platform.backup_snapshots SET verified_at = now(), "
             "verify_detail = :d WHERE id = :i"),
        {"d": body.detail.strip(), "i": snapshot_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.backup.verified", entity_type="backup_snapshot",
        entity_id=str(snapshot_id), actor_subject=admin.subject,
        reason=body.detail.strip(),
        before={"verified_at": (snap["verified_at"].isoformat()
                               if snap["verified_at"] else None)},
        after={"label": snap["label"]},
    )
    return _one(db, "SELECT id, label, verified_at, verify_detail FROM "
                    "platform.backup_snapshots WHERE id = :i",
                {"i": snapshot_id})


class RestoreIn(BaseModel):
    snapshot_id: uuid.UUID
    organization_id: uuid.UUID | None = None
    scope: str = Field(min_length=3, max_length=200)
    reason: str = Field(min_length=10, max_length=400)


@ops_router.post("/recovery/requests", status_code=201)
def request_restore(
    body: RestoreIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("recovery.request")),
):
    snap = _one(db, "SELECT id, label, verified_at FROM "
                    "platform.backup_snapshots WHERE id = :i",
                {"i": body.snapshot_id})
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    row = _one(
        db,
        """
        INSERT INTO platform.restore_requests
            (snapshot_id, organization_id, scope, reason, requested_by)
        VALUES (:s, :o, :sc, :r, :u)
        RETURNING id, status, scope, created_at
        """,
        {"s": snap["id"], "o": body.organization_id,
         "sc": body.scope.strip(), "r": body.reason.strip(),
         "u": admin.user_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.restore.requested", entity_type="restore_request",
        entity_id=str(row["id"]), organization_id=body.organization_id,
        actor_subject=admin.subject, reason=body.reason.strip(),
        after={"snapshot": snap["label"], "scope": body.scope.strip(),
               "snapshot_verified": snap["verified_at"] is not None},
    )
    return {**row, "snapshot_label": snap["label"]}


class RestoreDecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|refuse)$")
    note: str | None = None


@ops_router.post("/recovery/requests/{request_id}/decision")
def decide_restore(
    request_id: uuid.UUID,
    body: RestoreDecisionIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("recovery.approve")),
):
    """Approve or refuse somebody else's restore request.

    The requester cannot be the approver. The database says so too, but a
    constraint violation is a 500 and a person deserves a sentence.
    """
    r = _one(db, "SELECT id, status, requested_by, organization_id, scope "
                 "FROM platform.restore_requests WHERE id = :i",
             {"i": request_id})
    if r is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if r["status"] != "requested":
        raise HTTPException(status_code=409,
                            detail=f"This request is already {r['status']}.")
    if r["requested_by"] == admin.user_id:
        raise HTTPException(
            status_code=403,
            detail="A restore cannot be approved by the person who asked for "
                   "it. Somebody else with recovery approval has to sign it "
                   "off.")
    new_status = "approved" if body.decision == "approve" else "refused"
    db.execute(
        text("UPDATE platform.restore_requests SET status = :s, "
             "approved_by = :u, approved_at = now() WHERE id = :i"),
        {"s": new_status, "u": admin.user_id, "i": request_id},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action=f"platform.restore.{new_status}", entity_type="restore_request",
        entity_id=str(request_id), organization_id=r["organization_id"],
        actor_subject=admin.subject, reason=(body.note or "").strip() or None,
        before={"status": r["status"]}, after={"status": new_status},
    )
    return {"detail": f"Request {new_status}."}


# ================================================== 26 platform settings ==

@ops_router.get("/settings")
def get_settings(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("settings.manage")),
):
    """Platform defaults, feature flags, and what the runtime actually does.

    The third part is there because the first two are aspirational until
    something reads them. Showing a stored default beside the environment
    variable the services really use is the difference between a settings
    screen and a settings-shaped text box.
    """
    return {
        "settings": _rows(
            db,
            "SELECT s.key, s.value, s.description, s.updated_at, "
            "       u.display_name AS updated_by_name "
            "FROM platform.settings s "
            "LEFT JOIN iam.users u ON u.id = s.updated_by ORDER BY s.key",
        ),
        "flags": _rows(
            db,
            """
            SELECT f.code, f.name, f.description, f.enabled, f.updated_at,
                   (SELECT count(*) FROM platform.feature_overrides ov
                     WHERE ov.flag_code = f.code) AS overrides
            FROM platform.feature_flags f ORDER BY f.code
            """,
        ),
        "overrides": _rows(
            db,
            "SELECT ov.flag_code, ov.enabled, ov.note, ov.updated_at, "
            "       o.id AS organization_id, o.name AS tenant_name "
            "FROM platform.feature_overrides ov "
            "JOIN iam.organizations o ON o.id = ov.organization_id "
            "ORDER BY ov.flag_code, o.name",
        ),
        "runtime": [
            {"key": "SMTP", "value": "configured"
                if settings.mail_config.configured else "not configured",
             "note": "Read from the environment by each service."},
            {"key": "CREDENTIAL_ENCRYPTION_KEYS",
             "value": "present" if settings.credential_encryption_keys
                      else "absent",
             "note": "Without this, no credential can be sealed or opened."},
        ],
    }


class SettingIn(BaseModel):
    value: object


@ops_router.put("/settings/{key}")
def put_setting(
    key: str,
    body: SettingIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("settings.manage")),
):
    row = _one(db, "SELECT key, value FROM platform.settings WHERE key = :k",
               {"k": key})
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No such setting. Settings are declared in a migration, "
                   "not created from a screen, so that a typo cannot become a "
                   "key nothing reads.")
    db.execute(
        text("UPDATE platform.settings SET value = CAST(:v AS jsonb), "
             "updated_by = :u, updated_at = now() WHERE key = :k"),
        {"v": json.dumps(body.value), "u": admin.user_id,
         "k": key},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.setting.changed", entity_type="platform_setting",
        entity_id=key, actor_subject=admin.subject,
        before={"value": row["value"]}, after={"value": body.value},
    )
    return {"detail": "Saved."}


class FlagIn(BaseModel):
    enabled: bool
    organization_id: uuid.UUID | None = None
    note: str | None = None


@ops_router.put("/settings/flags/{code}")
def put_flag(
    code: str,
    body: FlagIn,
    request: Request,
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("settings.manage")),
):
    """Turn a flag on, either everywhere or for one tenant."""
    flag = _one(db, "SELECT code, enabled FROM platform.feature_flags "
                    "WHERE code = :c", {"c": code})
    if flag is None:
        raise HTTPException(status_code=404, detail="No such flag.")

    if body.organization_id:
        org = _org_or_404(db, body.organization_id)
        db.execute(
            text("""
                INSERT INTO platform.feature_overrides
                    (flag_code, organization_id, enabled, note)
                VALUES (:c, :o, :e, :n)
                ON CONFLICT (flag_code, organization_id) DO UPDATE
                   SET enabled = EXCLUDED.enabled, note = EXCLUDED.note,
                       updated_at = now()
            """),
            {"c": code, "o": org["id"], "e": body.enabled,
             "n": (body.note or "").strip() or None},
        )
        record_audit(
            db, correlation_id=correlation_id(request),
            action="platform.flag.override", entity_type="feature_flag",
            entity_id=code, organization_id=org["id"],
            actor_subject=admin.subject,
            after={"enabled": body.enabled, "tenant": org["name"]},
        )
        return {"detail": f"{code} is now "
                          f"{'on' if body.enabled else 'off'} for "
                          f"{org['name']}."}

    db.execute(
        text("UPDATE platform.feature_flags SET enabled = :e, "
             "updated_at = now() WHERE code = :c"),
        {"e": body.enabled, "c": code},
    )
    record_audit(
        db, correlation_id=correlation_id(request),
        action="platform.flag.changed", entity_type="feature_flag",
        entity_id=code, actor_subject=admin.subject,
        before={"enabled": flag["enabled"]}, after={"enabled": body.enabled},
    )
    return {"detail": f"{code} is now "
                      f"{'on' if body.enabled else 'off'} platform-wide."}


# ================================================= 27 platform analytics ==

@ops_router.get("/analytics")
def analytics(
    db: Session = Depends(get_session),
    admin: PlatformCaller = Depends(require_capability("analytics.view")),
):
    """Revenue mix, cohorts, churn and module adoption.

    Every figure here comes from a table that already exists, and where the
    honest answer is "not enough history yet" the response says so rather than
    drawing a flat line. A churn rate computed over four tenants and three
    weeks is a number with no information in it, and a chart makes it look
    like one with plenty.
    """
    mix = _rows(
        db,
        """
        SELECT pl.code AS plan_code, pl.name AS plan_name,
               count(*) AS tenants,
               sum(s.amount * s.quantity) AS mrr,
               count(*) FILTER (WHERE s.status = 'trialing') AS trialing,
               count(*) FILTER (WHERE s.status = 'active') AS active
        FROM billing.subscriptions s
        JOIN billing.plan_versions pv ON pv.id = s.plan_version_id
        JOIN billing.plans pl ON pl.id = pv.plan_id
        WHERE s.status IN ('trialing', 'active', 'past_due', 'grace')
        GROUP BY pl.code, pl.name ORDER BY sum(s.amount * s.quantity) DESC
        """,
    )
    cohorts = _rows(
        db,
        """
        SELECT to_char(date_trunc('month', o.created_at), 'Mon YYYY') AS cohort,
               date_trunc('month', o.created_at) AS cohort_start,
               count(*) AS signed_up,
               count(*) FILTER (WHERE o.status = 'active') AS still_active,
               count(s.id) FILTER (WHERE s.status = 'active') AS paying
        FROM iam.organizations o
        LEFT JOIN billing.subscriptions s
               ON s.organization_id = o.id
              AND s.status IN ('trialing', 'active', 'past_due', 'grace')
        GROUP BY 1, 2 ORDER BY 2
        """,
    )
    adoption = _rows(
        db,
        """
        SELECT m.module_code,
               count(*) FILTER (WHERE m.enabled) AS enabled_on,
               count(*) AS available_on,
               round(100.0 * count(*) FILTER (WHERE m.enabled)
                     / NULLIF(count(*), 0), 0) AS percent
        FROM iam.property_modules m
        GROUP BY m.module_code ORDER BY 2 DESC, 1
        """,
    )
    invoices = _rows(
        db,
        """
        SELECT to_char(date_trunc('month', i.issued_at), 'Mon YYYY') AS month,
               date_trunc('month', i.issued_at) AS month_start,
               count(*) AS invoices,
               sum(i.total) AS billed,
               sum(i.total) FILTER (WHERE i.status = 'paid') AS collected
        FROM billing.invoices i
        GROUP BY 1, 2 ORDER BY 2
        """,
    )
    totals = _one(
        db,
        """
        SELECT
          (SELECT count(*) FROM iam.organizations) AS tenants,
          (SELECT count(*) FROM iam.organizations
            WHERE status <> 'active') AS suspended,
          (SELECT count(*) FROM iam.properties WHERE status = 'active')
            AS properties,
          (SELECT COALESCE(sum(amount * quantity), 0)
             FROM billing.subscriptions
            WHERE status IN ('active', 'past_due', 'grace')) AS committed_mrr,
          (SELECT COALESCE(sum(amount * quantity), 0)
             FROM billing.subscriptions WHERE status = 'trialing')
            AS trialing_mrr,
          (SELECT min(created_at) FROM iam.organizations) AS first_signup
        """,
    )
    # Churn needs a departure to measure, and this platform has not had one.
    # Rather than print 0.0% -- which reads as "we measured it and it's great"
    # -- say what is missing and what would make the figure real.
    churned = _one(
        db,
        "SELECT count(*) AS n FROM billing.subscriptions "
        "WHERE status IN ('cancelled', 'canceled', 'ended')",
    )["n"]
    months = 0
    if totals and totals["first_signup"]:
        delta = _now() - totals["first_signup"]
        months = max(0, delta.days // 30)
    return {
        "totals": totals, "mix": mix, "cohorts": cohorts,
        "adoption": adoption, "invoices": invoices,
        "churn": {
            "cancelled": churned,
            "months_of_history": months,
            # Three renewal cycles is the usual floor before a churn rate
            # means anything at all.
            "measurable": churned > 0 and months >= 3,
            "note": "Churn needs departures and at least three renewal "
                    "cycles behind it. Neither is true yet, so no rate is "
                    "shown.",
        },
    }
