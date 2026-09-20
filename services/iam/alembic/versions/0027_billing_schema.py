"""SaaS billing: plans, subscriptions, entitlements, invoices and collection

Revision ID: 0027_billing_schema
Revises: 0026_platform_capabilities
Create Date: 2026-09-14

The commercial half of the platform, which until now rested on nothing.

This is a SaaS product: **the tenant chooses a plan and pays for it**, and the
platform oversees every tenant's subscription -- changing a plan, granting a
credit, suspending for non-payment. Both sides read these tables; only the
platform writes most of them.

Its own schema, deliberately. ``finance`` holds guest folios and the money a
hotel takes from *its* customers; this holds the money we take from the hotel.
One schema for both would mean a query that forgets a filter reports a guest's
deposit as platform revenue.

Four structural properties, each a rule the specification states turned into a
place a number is allowed to live:

1. **A price change cannot reach an existing contract.** Price lives on
   ``plan_versions``, immutable once published, and a subscription holds a
   foreign key to one specific version. Changing a price is a new row, never
   an UPDATE, so existing subscriptions keep what they signed until a
   ``subscription_change`` moves them. The subscription snapshots the amount
   as well -- a version could be corrected before anyone notices, and the
   contract should still be able to say what was agreed.

2. **A retried webhook cannot bill anybody twice.** ``provider_events`` takes
   ``PRIMARY KEY (provider, event_id)``, copied from
   ``finance.provider_events`` which already solves this for guest payments.
   ``invoices`` carries ``UNIQUE (subscription_id, period_start)`` so a
   scheduler that fires twice still produces one invoice.

3. **An issued invoice never changes.** Following ``finance.invoices``:
   snapshots at issue, cancel-not-delete, corrections by credit note with an
   approver distinct from the creator.

4. **A downgrade cannot be applied over usage that exceeds it.**
   ``usage_snapshots`` gives the daily number to compare against
   ``plan_version_limits``.

**No card data, ever.** Collection is provider-hosted: ``customers`` stores a
``provider_customer_id`` and nothing else about how somebody pays. A card
number has no column here and must never acquire one.

Tax is modelled generally rather than for one registration: the customer
carries ``gstin`` and ``state_code``, the invoice carries ``place_of_supply``,
and lines carry their own rate. That covers intra-state and inter-state supply
without this migration having to know where the company is registered.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027_billing_schema"
down_revision: str | None = "0026_platform_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "pms_app"

_SYSTEM = "tenancy.system_context()"
_ORG = "tenancy.org_visible(organization_id)"
#: Reachable through the subscription that owns the row.
_VIA_SUB = ("(tenancy.system_context() OR EXISTS (SELECT 1 "
            "FROM billing.subscriptions s WHERE s.id = subscription_id "
            "AND tenancy.org_visible(s.organization_id)))")
_VIA_INVOICE = ("(tenancy.system_context() OR EXISTS (SELECT 1 "
                "FROM billing.invoices i WHERE i.id = invoice_id "
                "AND tenancy.org_visible(i.organization_id)))")

#: table -> (read using, write using/check)
POLICIES: dict[str, tuple[str, str]] = {
    # Catalogue: not tenant data. A tenant choosing a plan has to be able to
    # read what the plans are.
    "plans": ("true", _SYSTEM),
    "plan_versions": ("true", _SYSTEM),
    "plan_version_limits": ("true", _SYSTEM),
    "plan_version_modules": ("true", _SYSTEM),
    # Contract: the tenant reads their own, the platform writes.
    "customers": (_ORG, _SYSTEM),
    "subscriptions": (_ORG, _SYSTEM),
    "subscription_changes": (_VIA_SUB, _SYSTEM),
    "entitlements": (_VIA_SUB, _SYSTEM),
    "usage_snapshots": (_ORG, _SYSTEM),
    # Money.
    "invoices": (_ORG, _SYSTEM),
    "invoice_lines": (_VIA_INVOICE, _SYSTEM),
    "credit_notes": (_VIA_INVOICE, _SYSTEM),
    "payments": (_ORG, _SYSTEM),
    "dunning_attempts": (_VIA_INVOICE, _SYSTEM),
    # Raw provider traffic. System only -- it is unvalidated input until
    # something has reconciled it, and it can name any tenant.
    "provider_events": (_SYSTEM, _SYSTEM),
}

TABLES = """
CREATE SCHEMA IF NOT EXISTS billing;

-- ------------------------------------------------------------- catalogue --

CREATE TABLE IF NOT EXISTS billing.plans (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        varchar(40) NOT NULL UNIQUE,
    name        varchar(120) NOT NULL,
    summary     varchar(300) NOT NULL DEFAULT '',
    -- Ordering for the plan picker, so it is a product decision rather than
    -- whatever order the rows came back in.
    sort_order  integer NOT NULL DEFAULT 0,
    status      varchar(20) NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'retired')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Deliberately carries no price: see the module docstring, property 1.
CREATE TABLE IF NOT EXISTS billing.plan_versions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id       uuid NOT NULL REFERENCES billing.plans(id),
    version_no    integer NOT NULL,
    -- Per property per cycle. The billable unit is a property: a tenant with
    -- three hotels pays three times, which is what the sample figures in the
    -- specification reconcile to.
    amount        numeric(14,2) NOT NULL CHECK (amount >= 0),
    currency      char(3) NOT NULL DEFAULT 'INR',
    billing_cycle varchar(12) NOT NULL
                    CHECK (billing_cycle IN ('monthly', 'annual')),
    trial_days    integer NOT NULL DEFAULT 0 CHECK (trial_days >= 0),
    status        varchar(12) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'published', 'retired')),
    published_at  timestamptz NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_plan_version UNIQUE (plan_id, version_no),
    -- A published version must say when. Nothing may be published without a
    -- date, because the date is what an invoice line refers back to.
    CONSTRAINT ck_published_has_date
        CHECK (status <> 'published' OR published_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS billing.plan_version_limits (
    plan_version_id uuid NOT NULL REFERENCES billing.plan_versions(id)
                      ON DELETE CASCADE,
    metric_code     varchar(40) NOT NULL,
    -- NULL means unlimited, which is different from 0 and must stay so.
    limit_value     integer NULL CHECK (limit_value IS NULL OR limit_value >= 0),
    PRIMARY KEY (plan_version_id, metric_code)
);

CREATE TABLE IF NOT EXISTS billing.plan_version_modules (
    plan_version_id uuid NOT NULL REFERENCES billing.plan_versions(id)
                      ON DELETE CASCADE,
    -- Matches iam.property_modules.module_code. The plan sets the ceiling;
    -- that table records the tenant's own on/off within it.
    module_code     varchar(50) NOT NULL,
    PRIMARY KEY (plan_version_id, module_code)
);

-- -------------------------------------------------------------- contract --

CREATE TABLE IF NOT EXISTS billing.customers (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      uuid NOT NULL UNIQUE
                           REFERENCES iam.organizations(id),
    legal_name           varchar(200) NOT NULL,
    billing_email        varchar(200) NOT NULL,
    phone                varchar(40) NULL,
    address_line         varchar(300) NULL,
    city                 varchar(120) NULL,
    -- Place of supply is decided from this, so it is not decoration.
    state_code           varchar(10) NULL,
    postal_code          varchar(20) NULL,
    country              char(2) NOT NULL DEFAULT 'IN',
    gstin                varchar(20) NULL,
    -- The payment provider's own handle for this customer. The ONLY thing
    -- stored about how they pay: checkout is provider-hosted and no card
    -- number, token or expiry has a column here.
    provider             varchar(30) NULL,
    provider_customer_id varchar(120) NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_customer_provider UNIQUE (provider, provider_customer_id)
);

CREATE TABLE IF NOT EXISTS billing.subscriptions (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id          uuid NOT NULL REFERENCES iam.organizations(id),
    -- Pinned to a version, never to a plan.
    plan_version_id          uuid NOT NULL REFERENCES billing.plan_versions(id),
    status                   varchar(16) NOT NULL
        CHECK (status IN ('trialing', 'active', 'past_due', 'grace',
                          'cancelled', 'expired')),
    -- Snapshot of what was agreed, independent of the version row.
    amount                   numeric(14,2) NOT NULL CHECK (amount >= 0),
    currency                 char(3) NOT NULL DEFAULT 'INR',
    billing_cycle            varchar(12) NOT NULL,
    quantity                 integer NOT NULL DEFAULT 1 CHECK (quantity >= 0),
    started_on               date NOT NULL,
    trial_ends_on            date NULL,
    current_period_start     date NULL,
    current_period_end       date NULL,
    -- How long service continues after a failed payment before access stops.
    grace_until              date NULL,
    cancel_at                date NULL,
    cancelled_at             timestamptz NULL,
    cancel_reason            varchar(300) NULL,
    provider_subscription_id varchar(120) NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    version                  bigint NOT NULL DEFAULT 0
);

-- One live subscription per tenant. A partial index rather than a plain
-- unique, because a tenant that cancels and later returns must be able to
-- have a second row without the first being deleted.
CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_live
    ON billing.subscriptions (organization_id)
    WHERE status IN ('trialing', 'active', 'past_due', 'grace');

CREATE TABLE IF NOT EXISTS billing.subscription_changes (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id      uuid NOT NULL REFERENCES billing.subscriptions(id),
    from_plan_version_id uuid NULL REFERENCES billing.plan_versions(id),
    to_plan_version_id   uuid NOT NULL REFERENCES billing.plan_versions(id),
    effective_on         date NOT NULL,
    proration_amount     numeric(14,2) NOT NULL DEFAULT 0,
    -- A previewed change is a row, not a calculation thrown away: it is what
    -- lets somebody confirm later exactly what they were shown.
    status               varchar(16) NOT NULL DEFAULT 'previewed'
        CHECK (status IN ('previewed', 'scheduled', 'applied', 'cancelled')),
    requested_by         uuid NULL REFERENCES iam.users(id),
    approved_by          uuid NULL REFERENCES iam.users(id),
    reason               varchar(300) NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    applied_at           timestamptz NULL
);

CREATE TABLE IF NOT EXISTS billing.entitlements (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id uuid NOT NULL REFERENCES billing.subscriptions(id)
                      ON DELETE CASCADE,
    kind            varchar(12) NOT NULL CHECK (kind IN ('limit', 'module')),
    code            varchar(50) NOT NULL,
    -- NULL for a module (presence is the grant) and for an unlimited quota.
    value           integer NULL,
    -- 'plan' or 'override', so "why does this tenant have this" is answerable
    -- without reading the change history.
    source          varchar(12) NOT NULL DEFAULT 'plan'
                      CHECK (source IN ('plan', 'override')),
    note            varchar(300) NULL,
    effective_from  date NOT NULL DEFAULT CURRENT_DATE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_entitlement UNIQUE (subscription_id, kind, code)
);

CREATE TABLE IF NOT EXISTS billing.usage_snapshots (
    organization_id uuid NOT NULL REFERENCES iam.organizations(id),
    captured_on     date NOT NULL,
    metric_code     varchar(40) NOT NULL,
    value           integer NOT NULL CHECK (value >= 0),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, captured_on, metric_code)
);

-- ----------------------------------------------------------------- money --

CREATE TABLE IF NOT EXISTS billing.invoices (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid NOT NULL REFERENCES iam.organizations(id),
    subscription_id   uuid NULL REFERENCES billing.subscriptions(id),
    series            varchar(20) NOT NULL DEFAULT 'SAAS',
    number            integer NOT NULL,
    status            varchar(16) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'issued', 'paid', 'void', 'uncollectible')),
    period_start      date NULL,
    period_end        date NULL,
    due_on            date NULL,
    currency          char(3) NOT NULL DEFAULT 'INR',
    subtotal          numeric(14,2) NOT NULL DEFAULT 0,
    tax_total         numeric(14,2) NOT NULL DEFAULT 0,
    total             numeric(14,2) NOT NULL DEFAULT 0,
    amount_paid       numeric(14,2) NOT NULL DEFAULT 0,
    -- Decides whether tax is intra-state or inter-state. Stored rather than
    -- derived at read time, because the customer's address can change and an
    -- issued invoice may not.
    place_of_supply   varchar(10) NULL,
    customer_snapshot jsonb NULL,
    totals_snapshot   jsonb NULL,
    issued_at         timestamptz NULL,
    paid_at           timestamptz NULL,
    cancelled_at      timestamptz NULL,
    cancelled_by      uuid NULL REFERENCES iam.users(id),
    cancel_reason     varchar(300) NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_invoice_number UNIQUE (series, number),
    -- The billing run's protection from itself: a scheduler that fires twice,
    -- or a retry after a timeout, still produces one invoice for a period.
    CONSTRAINT uq_invoice_period UNIQUE (subscription_id, period_start),
    CONSTRAINT ck_issued_has_date
        CHECK (status = 'draft' OR issued_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS billing.invoice_lines (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      uuid NOT NULL REFERENCES billing.invoices(id)
                      ON DELETE CASCADE,
    kind            varchar(16) NOT NULL DEFAULT 'subscription'
        CHECK (kind IN ('subscription', 'proration', 'adjustment')),
    description     varchar(300) NOT NULL,
    -- Properties, for a per-property plan.
    quantity        numeric(10,2) NOT NULL DEFAULT 1,
    unit_amount     numeric(14,2) NOT NULL DEFAULT 0,
    amount          numeric(14,2) NOT NULL DEFAULT 0,
    tax_rate        numeric(6,3) NOT NULL DEFAULT 0,
    tax_amount      numeric(14,2) NOT NULL DEFAULT 0,
    -- Lets an invoice explain its own price rather than asserting it.
    plan_version_id uuid NULL REFERENCES billing.plan_versions(id),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS billing.credit_notes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id  uuid NOT NULL REFERENCES billing.invoices(id),
    number      integer NOT NULL,
    amount      numeric(14,2) NOT NULL CHECK (amount > 0),
    reason      varchar(300) NOT NULL,
    status      varchar(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    created_by  uuid NULL REFERENCES iam.users(id),
    -- Separate from created_by on purpose: an approval that the requester
    -- performed is not an approval.
    approved_by uuid NULL REFERENCES iam.users(id),
    approved_at timestamptz NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_credit_note_number UNIQUE (number),
    CONSTRAINT ck_approved_has_approver
        CHECK (status <> 'approved'
               OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS billing.payments (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     uuid NOT NULL REFERENCES iam.organizations(id),
    invoice_id          uuid NULL REFERENCES billing.invoices(id),
    provider            varchar(30) NOT NULL,
    provider_payment_id varchar(120) NOT NULL,
    amount              numeric(14,2) NOT NULL CHECK (amount > 0),
    currency            char(3) NOT NULL DEFAULT 'INR',
    status              varchar(16) NOT NULL
        CHECK (status IN ('pending', 'captured', 'failed', 'refunded')),
    method              varchar(30) NULL,
    received_at         timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_payment_provider UNIQUE (provider, provider_payment_id)
);

-- The idempotency ledger, identical in shape to finance.provider_events.
-- A replayed webhook violates the primary key and is discarded before it can
-- create an invoice or a payment.
CREATE TABLE IF NOT EXISTS billing.provider_events (
    provider    varchar(30) NOT NULL,
    event_id    varchar(120) NOT NULL,
    event_type  varchar(80) NOT NULL,
    payload     jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    outcome     varchar(30) NOT NULL,
    payment_id  uuid NULL REFERENCES billing.payments(id),
    detail      text NULL,
    PRIMARY KEY (provider, event_id)
);

CREATE TABLE IF NOT EXISTS billing.dunning_attempts (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id uuid NOT NULL REFERENCES billing.invoices(id),
    attempt_no integer NOT NULL,
    channel    varchar(20) NOT NULL DEFAULT 'email',
    sent_at    timestamptz NOT NULL DEFAULT now(),
    outcome    varchar(30) NOT NULL DEFAULT 'sent',
    detail     varchar(300) NULL,
    CONSTRAINT uq_dunning_attempt UNIQUE (invoice_id, attempt_no)
);

CREATE INDEX IF NOT EXISTS ix_invoices_org ON billing.invoices (organization_id, status);
CREATE INDEX IF NOT EXISTS ix_subscriptions_org ON billing.subscriptions (organization_id);
CREATE INDEX IF NOT EXISTS ix_usage_org_date ON billing.usage_snapshots (organization_id, captured_on DESC);
"""

#: Three plans, priced per property per month. Example figures, not a
#: commercial recommendation -- the specification says its own sample prices
#: are not a published price list, and neither are these.
PLANS = [
    ("starter", "Starter", "One property, the essentials.", 1,
     "2499.00", 14,
     {"properties": 1, "rooms": 30, "active_users": 5},
     ("front_desk", "reservations", "housekeeping", "guests", "reports")),
    ("growth", "Growth", "Several properties, channels and rate rules.", 2,
     "4999.00", 14,
     {"properties": 5, "rooms": 150, "active_users": 25},
     ("front_desk", "reservations", "housekeeping", "guests", "reports",
      "distribution", "rates", "pos")),
    ("scale", "Scale", "Unlimited estate, every module.", 3,
     "8999.00", 0,
     {"properties": None, "rooms": None, "active_users": None},
     ("front_desk", "reservations", "housekeeping", "guests", "reports",
      "distribution", "rates", "pos", "ai_center", "administration")),
]

CAPABILITIES = [
    ("billing.view", "See subscriptions, invoices and payments across tenants"),
    ("subscription.manage", "Change a tenant's plan, trial or lifecycle"),
    ("plan.manage", "Create and publish plan versions"),
    ("invoice.manage", "Issue, void and reconcile subscription invoices"),
    ("credit.approve", "Approve a credit note or refund"),
]


def _q(value: str) -> str:
    """A single-quoted SQL literal with its own quotes doubled.

    These statements are interpolated rather than parameterised because
    alembic's op.execute takes no bind parameters, so every literal that could
    contain an apostrophe has to come through here. "Change a tenant's plan"
    closed the string early and took the statement with it.
    """
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute(TABLES)

    for code, name, summary, order, amount, trial, limits, modules in PLANS:
        op.execute(
            f"""
            INSERT INTO billing.plans (code, name, summary, sort_order)
            VALUES ({_q(code)}, {_q(name)}, {_q(summary)}, {order})
            ON CONFLICT (code) DO UPDATE
               SET name = EXCLUDED.name, summary = EXCLUDED.summary,
                   sort_order = EXCLUDED.sort_order, updated_at = now()
            """
        )
        op.execute(
            f"""
            INSERT INTO billing.plan_versions
                (plan_id, version_no, amount, currency, billing_cycle,
                 trial_days, status, published_at)
            SELECT p.id, 1, {amount}, 'INR', 'monthly', {trial},
                   'published', now()
            FROM billing.plans p WHERE p.code = '{code}'
            ON CONFLICT (plan_id, version_no) DO NOTHING
            """
        )
        for metric, value in limits.items():
            op.execute(
                f"""
                INSERT INTO billing.plan_version_limits
                    (plan_version_id, metric_code, limit_value)
                SELECT v.id, '{metric}', {'NULL' if value is None else value}
                FROM billing.plan_versions v
                JOIN billing.plans p ON p.id = v.plan_id
                WHERE p.code = '{code}' AND v.version_no = 1
                ON CONFLICT DO NOTHING
                """
            )
        for module in modules:
            op.execute(
                f"""
                INSERT INTO billing.plan_version_modules
                    (plan_version_id, module_code)
                SELECT v.id, '{module}'
                FROM billing.plan_versions v
                JOIN billing.plans p ON p.id = v.plan_id
                WHERE p.code = '{code}' AND v.version_no = 1
                ON CONFLICT DO NOTHING
                """
            )

    # The platform capabilities these screens need, granted to Billing admin
    # and to Platform owner. Nobody else -- a support agent reading a
    # customer's invoices is not support, it is browsing.
    for code, description in CAPABILITIES:
        op.execute(
            "INSERT INTO iam.platform_permissions (code, description) "
            f"VALUES ({_q(code)}, {_q(description)}) "
            "ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description"
        )
    op.execute(
        f"""
        INSERT INTO iam.platform_role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM iam.platform_roles r
        JOIN iam.platform_permissions p
          ON p.code IN ({", ".join(f"'{c}'" for c, _ in CAPABILITIES)})
        WHERE r.code IN ('platform_owner', 'billing_admin')
        ON CONFLICT DO NOTHING
        """
    )
    # Support agents see that a tenant has a subscription, not its money.
    op.execute(
        """
        INSERT INTO iam.platform_role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM iam.platform_roles r
        JOIN iam.platform_permissions p ON p.code = 'billing.view'
        WHERE r.code = 'support_agent'
        ON CONFLICT DO NOTHING
        """
    )

    missing = sorted(
        set(
            r[0] for r in op.get_bind().exec_driver_sql(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'billing' AND c.relkind = 'r'"
            ).fetchall()
        ) - set(POLICIES)
    )
    if missing:
        raise RuntimeError(f"billing tables with no policy: {missing}")

    for table, (read, write) in POLICIES.items():
        op.execute(f"ALTER TABLE billing.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE billing.{table} FORCE ROW LEVEL SECURITY")
        for name in ("tenant_read", "tenant_insert", "tenant_update",
                     "tenant_delete"):
            op.execute(f"DROP POLICY IF EXISTS {name} ON billing.{table}")
        op.execute(
            f"CREATE POLICY tenant_read ON billing.{table} "
            f"FOR SELECT USING ({read})")
        op.execute(
            f"CREATE POLICY tenant_insert ON billing.{table} "
            f"FOR INSERT WITH CHECK ({write})")
        op.execute(
            f"CREATE POLICY tenant_update ON billing.{table} FOR UPDATE "
            f"USING ({write}) WITH CHECK ({write})")
        op.execute(
            f"CREATE POLICY tenant_delete ON billing.{table} "
            f"FOR DELETE USING ({write})")

    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT USAGE ON SCHEMA billing TO {RUNTIME_ROLE};
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON ALL TABLES IN SCHEMA billing TO {RUNTIME_ROLE};
            ALTER DEFAULT PRIVILEGES IN SCHEMA billing
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RUNTIME_ROLE};
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM iam.platform_role_permissions rp
        USING iam.platform_permissions p
        WHERE rp.permission_id = p.id
          AND p.code IN ('billing.view', 'subscription.manage', 'plan.manage',
                         'invoice.manage', 'credit.approve')
        """
    )
    op.execute(
        """
        DELETE FROM iam.platform_permissions
        WHERE code IN ('billing.view', 'subscription.manage', 'plan.manage',
                       'invoice.manage', 'credit.approve')
        """
    )
    op.execute("DROP SCHEMA IF EXISTS billing CASCADE")
