"""The platform's own operational data: providers, domains, messaging,
support, recovery and settings

Revision ID: 0032_platform_operations
Revises: 0031_per_tenant_pricing
Create Date: 2026-09-14

The seven modules the design pack specifies and this system had nothing
behind. Each was a screen with no table, which is why each was a placeholder
rather than a half-working page.

One schema rather than six, because they share an owner. Everything here is
**operated by the platform**, not by a tenant: a tenant may read its own
support tickets and its own booking domain, and writes nothing. That is the
same asymmetry ``billing`` uses and it is enforced the same way -- reads
scoped by ``tenancy.org_visible``, writes reserved to system context.

Notes on the parts that carry risk:

**Provider credentials are sealed, and this migration stores none.** The
column holds Fernet ciphertext from ``chirala_common.secretbox``, the same
mechanism protecting tenant payment credentials and second factors. Staging
and production are separate rows with a unique constraint across
(provider, environment) so one cannot silently overwrite the other -- a
deployment pointed at a staging key that thinks it is production is how test
bookings reach a real channel.

**Support access is a request and an approval, not a session.** The grant
records who asked, what scope, why, for how long, and who approved. There is
deliberately **no impersonation mechanism** in this migration or anywhere
else: a row saying access was approved is a record, and the machinery that
would act on it needs designing with the tenant in the room. Approving is not
entering.

**Restore requests carry a requester and an approver, and a check constraint
refuses them being the same person.** The pack asks for that separation and a
convention would not survive the first urgent Friday.

**Feature flags default off and carry an allowlist.** A flag that defaults on
is a release nobody chose.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032_platform_operations"
down_revision: str | None = "0031_per_tenant_pricing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "pms_app"
_SYSTEM = "tenancy.system_context()"
_ORG = "tenancy.org_visible(organization_id)"
_VIA_TICKET = ("(tenancy.system_context() OR EXISTS (SELECT 1 "
               "FROM platform.support_tickets t WHERE t.id = ticket_id "
               "AND tenancy.org_visible(t.organization_id)))")

#: table -> (read, write)
POLICIES: dict[str, tuple[str, str]] = {
    "provider_credentials": (_SYSTEM, _SYSTEM),
    "booking_domains": (_ORG, _SYSTEM),
    "message_templates": ("true", _SYSTEM),
    "message_deliveries": (_ORG, _SYSTEM),
    "support_tickets": (_ORG, _SYSTEM),
    "support_messages": (_VIA_TICKET, _SYSTEM),
    "support_access_grants": (_ORG, _SYSTEM),
    "backup_snapshots": (_SYSTEM, _SYSTEM),
    "restore_requests": (_SYSTEM, _SYSTEM),
    "settings": ("true", _SYSTEM),
    "feature_flags": ("true", _SYSTEM),
    "feature_overrides": (_ORG, _SYSTEM),
}

TABLES = """
CREATE SCHEMA IF NOT EXISTS platform;

-- ------------------------------------------------- 16 provider setup ------

CREATE TABLE IF NOT EXISTS platform.provider_credentials (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider     varchar(40) NOT NULL,
    environment  varchar(20) NOT NULL
                   CHECK (environment IN ('sandbox', 'production')),
    label        varchar(120) NOT NULL,
    -- Fernet ciphertext. A readable credential here would make a database
    -- dump equivalent to the provider account.
    secret       text NULL,
    -- The non-secret half: account ids, endpoints, webhook paths.
    config       jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       varchar(16) NOT NULL DEFAULT 'configured'
                   CHECK (status IN ('configured', 'unverified', 'disabled')),
    last_verified_at timestamptz NULL,
    last_verify_detail varchar(300) NULL,
    rotated_at   timestamptz NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    -- One row per provider per environment. Without this a production key
    -- can be quietly shadowed by a sandbox one of the same name.
    CONSTRAINT uq_provider_environment UNIQUE (provider, environment)
);

-- ------------------------------------------- 18 booking engine domains ----

CREATE TABLE IF NOT EXISTS platform.booking_domains (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES iam.organizations(id),
    property_id     uuid NOT NULL REFERENCES iam.properties(id),
    hostname        varchar(253) NOT NULL UNIQUE,
    status          varchar(20) NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'verified', 'live',
                                        'failed', 'retired')),
    -- Proof the tenant controls the name, checked before anything is served
    -- from it: a hostname somebody else owns is a phishing page with our
    -- booking engine behind it.
    verification_token varchar(80) NOT NULL,
    verification_method varchar(20) NOT NULL DEFAULT 'dns-txt',
    verified_at     timestamptz NULL,
    tls_status      varchar(20) NOT NULL DEFAULT 'none'
                      CHECK (tls_status IN ('none', 'pending', 'issued',
                                            'expiring', 'failed')),
    tls_expires_at  timestamptz NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------- 19 messaging ----

CREATE TABLE IF NOT EXISTS platform.message_templates (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        varchar(60) NOT NULL,
    version     integer NOT NULL DEFAULT 1,
    name        varchar(150) NOT NULL,
    channel     varchar(20) NOT NULL DEFAULT 'email'
                  CHECK (channel IN ('email', 'sms')),
    subject     varchar(300) NULL,
    body_text   text NOT NULL DEFAULT '',
    body_html   text NULL,
    -- The names a template may interpolate. Declared so an editor can be
    -- checked against them: a typo'd variable renders as empty space in
    -- somebody's inbox, and nothing else would catch it.
    variables   text[] NOT NULL DEFAULT '{}',
    status      varchar(12) NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'published', 'retired')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_template_version UNIQUE (code, version)
);

CREATE TABLE IF NOT EXISTS platform.message_deliveries (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NULL REFERENCES iam.organizations(id),
    template_code   varchar(60) NULL,
    channel         varchar(20) NOT NULL DEFAULT 'email',
    recipient       varchar(320) NOT NULL,
    subject         varchar(300) NULL,
    status          varchar(16) NOT NULL
                      CHECK (status IN ('sent', 'failed', 'bounced',
                                        'suppressed')),
    detail          varchar(400) NULL,
    attempts        integer NOT NULL DEFAULT 1,
    sent_at         timestamptz NOT NULL DEFAULT now()
);

-- --------------------------------------------------------- 20/21 support --

CREATE TABLE IF NOT EXISTS platform.support_tickets (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference       varchar(20) NOT NULL UNIQUE,
    organization_id uuid NOT NULL REFERENCES iam.organizations(id),
    property_id     uuid NULL REFERENCES iam.properties(id),
    subject         varchar(200) NOT NULL,
    priority        varchar(12) NOT NULL DEFAULT 'normal'
                      CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    status          varchar(16) NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'waiting', 'resolved',
                                        'closed')),
    assigned_to     uuid NULL REFERENCES iam.users(id),
    opened_by       varchar(200) NULL,
    first_response_due timestamptz NULL,
    resolution_due  timestamptz NULL,
    resolved_at     timestamptz NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.support_messages (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id  uuid NOT NULL REFERENCES platform.support_tickets(id)
                 ON DELETE CASCADE,
    author     varchar(200) NOT NULL,
    from_side  varchar(12) NOT NULL CHECK (from_side IN ('tenant', 'platform')),
    body       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- A request and an approval. NOT a session, and not a mechanism: nothing in
-- this system acts on an approved grant, because consented, time-boxed
-- access into a customer's data needs designing with the customer in the
-- room. Recording the decision is the honest half to build first.
CREATE TABLE IF NOT EXISTS platform.support_access_grants (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id       uuid NULL REFERENCES platform.support_tickets(id),
    organization_id uuid NOT NULL REFERENCES iam.organizations(id),
    property_id     uuid NULL REFERENCES iam.properties(id),
    requested_by    uuid NOT NULL REFERENCES iam.users(id),
    reason          varchar(400) NOT NULL,
    scope           text[] NOT NULL DEFAULT '{}',
    minutes         integer NOT NULL DEFAULT 15 CHECK (minutes BETWEEN 5 AND 480),
    status          varchar(16) NOT NULL DEFAULT 'requested'
                      CHECK (status IN ('requested', 'approved', 'refused',
                                        'expired', 'revoked')),
    -- The tenant's own person, never platform staff.
    approved_by     uuid NULL REFERENCES iam.users(id),
    approved_at     timestamptz NULL,
    expires_at      timestamptz NULL,
    revoked_at      timestamptz NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_grant_approved_has_approver
        CHECK (status <> 'approved'
               OR (approved_by IS NOT NULL AND approved_at IS NOT NULL
                   AND expires_at IS NOT NULL)),
    -- The requester may not approve their own access. Stated here rather
    -- than left to the route, because this is the whole control.
    CONSTRAINT ck_grant_separate_approver
        CHECK (approved_by IS NULL OR approved_by <> requested_by)
);

-- ------------------------------------------------- 25 backups & recovery --

CREATE TABLE IF NOT EXISTS platform.backup_snapshots (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label         varchar(150) NOT NULL,
    scope         varchar(20) NOT NULL DEFAULT 'platform'
                    CHECK (scope IN ('platform', 'tenant')),
    organization_id uuid NULL REFERENCES iam.organizations(id),
    taken_at      timestamptz NOT NULL,
    size_bytes    bigint NULL,
    location      varchar(400) NULL,
    -- A backup nobody has restored is a hope, not a backup.
    verified_at   timestamptz NULL,
    verify_detail varchar(300) NULL,
    retain_until  date NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.restore_requests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id     uuid NOT NULL REFERENCES platform.backup_snapshots(id),
    organization_id uuid NULL REFERENCES iam.organizations(id),
    scope           varchar(200) NOT NULL,
    reason          varchar(400) NOT NULL,
    requested_by    uuid NOT NULL REFERENCES iam.users(id),
    status          varchar(16) NOT NULL DEFAULT 'requested'
                      CHECK (status IN ('requested', 'approved', 'refused',
                                        'verified', 'completed', 'cancelled')),
    approved_by     uuid NULL REFERENCES iam.users(id),
    approved_at     timestamptz NULL,
    verified_at     timestamptz NULL,
    completed_at    timestamptz NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- The pack asks for an approver distinct from the requester, and a
    -- convention would not survive the first urgent Friday night.
    CONSTRAINT ck_restore_separate_approver
        CHECK (approved_by IS NULL OR approved_by <> requested_by),
    CONSTRAINT ck_restore_approved_has_approver
        CHECK (status NOT IN ('approved', 'verified', 'completed')
               OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);

-- ---------------------------------------------------- 26 platform settings --

CREATE TABLE IF NOT EXISTS platform.settings (
    key         varchar(80) PRIMARY KEY,
    value       jsonb NOT NULL,
    description varchar(300) NOT NULL DEFAULT '',
    updated_by  uuid NULL REFERENCES iam.users(id),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.feature_flags (
    code        varchar(60) PRIMARY KEY,
    name        varchar(150) NOT NULL,
    description varchar(300) NOT NULL DEFAULT '',
    -- Off. A flag that defaults on is a release nobody chose.
    enabled     boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.feature_overrides (
    flag_code       varchar(60) NOT NULL
                      REFERENCES platform.feature_flags(code) ON DELETE CASCADE,
    organization_id uuid NOT NULL REFERENCES iam.organizations(id),
    enabled         boolean NOT NULL,
    note            varchar(300) NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (flag_code, organization_id)
);

CREATE INDEX IF NOT EXISTS ix_tickets_org ON platform.support_tickets (organization_id, status);
CREATE INDEX IF NOT EXISTS ix_deliveries_sent ON platform.message_deliveries (sent_at DESC);
CREATE INDEX IF NOT EXISTS ix_domains_property ON platform.booking_domains (property_id);
"""

CAPABILITIES = [
    ("provider.manage", "Configure integration provider credentials"),
    ("domain.manage", "Manage booking engine domains"),
    ("messaging.manage", "Edit message templates and read delivery logs"),
    ("support.view", "Read support tickets and conversations"),
    ("support.manage", "Assign, reply to and resolve support tickets"),
    ("support.request_access", "Request scoped access to a tenant's data"),
    ("recovery.request", "Request a restore from a backup"),
    ("recovery.approve", "Approve a restore request raised by somebody else"),
    ("settings.manage", "Change platform defaults and feature flags"),
    ("analytics.view", "Read platform adoption and revenue analytics"),
]

ROLE_GRANTS = {
    "platform_owner": [c for c, _ in CAPABILITIES],
    "billing_admin": ["analytics.view", "support.view"],
    "support_agent": ["support.view", "support.manage",
                      "support.request_access", "messaging.manage"],
    "platform_operator": ["provider.manage", "domain.manage", "support.view",
                          "recovery.request", "recovery.approve",
                          "settings.manage"],
    "onboarding_specialist": ["domain.manage", "support.view"],
}

SETTINGS = [
    ("platform.name", '"Chirala Bay PMS"', "What the product is called in email."),
    ("platform.support_email", '""', "Where staff who cannot sign in should turn."),
    ("platform.default_timezone", '"Asia/Kolkata"', "Timezone for a new property."),
    ("platform.default_currency", '"INR"', "Currency for a new property."),
    ("platform.locales", '["en"]', "Locales the interface is offered in."),
]

FLAGS = [
    ("tenant_billing_portal", "Tenant billing portal",
     "Let tenant owners choose and change their own plan."),
    ("channel_auto_retry", "Automatic channel retry",
     "Retry failed channel pushes without an operator."),
    ("support_access_requests", "Support access requests",
     "Allow staff to request scoped access to a tenant's data."),
]


def _q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute(TABLES)

    for code, description in CAPABILITIES:
        op.execute(
            "INSERT INTO iam.platform_permissions (code, description) "
            f"VALUES ({_q(code)}, {_q(description)}) "
            "ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description"
        )
    for role, caps in ROLE_GRANTS.items():
        codes = ", ".join(_q(c) for c in caps)
        op.execute(
            f"""
            INSERT INTO iam.platform_role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM iam.platform_roles r
            JOIN iam.platform_permissions p ON p.code IN ({codes})
            WHERE r.code = {_q(role)}
            ON CONFLICT DO NOTHING
            """
        )

    for key, value, desc in SETTINGS:
        op.execute(
            "INSERT INTO platform.settings (key, value, description) "
            f"VALUES ({_q(key)}, {_q(value)}::jsonb, {_q(desc)}) "
            "ON CONFLICT (key) DO NOTHING"
        )
    for code, name, desc in FLAGS:
        op.execute(
            "INSERT INTO platform.feature_flags (code, name, description) "
            f"VALUES ({_q(code)}, {_q(name)}, {_q(desc)}) "
            "ON CONFLICT (code) DO NOTHING"
        )

    missing = sorted(
        {r[0] for r in op.get_bind().exec_driver_sql(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'platform' AND c.relkind = 'r'").fetchall()}
        - set(POLICIES))
    if missing:
        raise RuntimeError(f"platform tables with no policy: {missing}")

    for table, (read, write) in POLICIES.items():
        op.execute(f"ALTER TABLE platform.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE platform.{table} FORCE ROW LEVEL SECURITY")
        for name in ("tenant_read", "tenant_insert", "tenant_update",
                     "tenant_delete"):
            op.execute(f"DROP POLICY IF EXISTS {name} ON platform.{table}")
        op.execute(f"CREATE POLICY tenant_read ON platform.{table} "
                   f"FOR SELECT USING ({read})")
        op.execute(f"CREATE POLICY tenant_insert ON platform.{table} "
                   f"FOR INSERT WITH CHECK ({write})")
        op.execute(f"CREATE POLICY tenant_update ON platform.{table} "
                   f"FOR UPDATE USING ({write}) WITH CHECK ({write})")
        op.execute(f"CREATE POLICY tenant_delete ON platform.{table} "
                   f"FOR DELETE USING ({write})")

    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT USAGE ON SCHEMA platform TO {RUNTIME_ROLE};
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON ALL TABLES IN SCHEMA platform TO {RUNTIME_ROLE};
            ALTER DEFAULT PRIVILEGES IN SCHEMA platform
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RUNTIME_ROLE};
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    codes = ", ".join(_q(c) for c, _ in CAPABILITIES)
    op.execute(
        f"DELETE FROM iam.platform_role_permissions rp "
        f"USING iam.platform_permissions p "
        f"WHERE rp.permission_id = p.id AND p.code IN ({codes})"
    )
    op.execute(f"DELETE FROM iam.platform_permissions WHERE code IN ({codes})")
    op.execute("DROP SCHEMA IF EXISTS platform CASCADE")
