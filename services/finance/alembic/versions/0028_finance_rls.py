"""Row-level security on every finance table

Revision ID: 0028_finance_rls
Revises: 0027_expenses_owners
Create Date: 2026-09-14

Stage 2 of tenant isolation in the database (schema blueprint §12). Until now a
tenant's money was kept apart only by the application: every route checks the
tenant, and every query filters by property. One query that forgot its filter
would have read another hotel's folios, and nothing underneath would have
noticed. From here the database refuses it too.

**Who a transaction acts for** is set by the application, transaction-local,
from the authenticated caller -- ``chirala_common.db.bind_tenant_context`` --
never from anything a client sends. A row is visible when its organisation is
that tenant's. With no tenant bound, nothing is visible: an empty setting
matches no row, never every row.

**System context** (``app.system = 'on'``, via ``system_context``) sees every
tenant, and exists for the few jobs that are above the tenant boundary by
nature: resolving which tenant a payment callback belongs to, the night-audit
sweep that lists properties, and platform administration. Each use names its
reason.

**Tables without an organisation column** are secured through their parent:
a tax line is visible when its folio entry is, an invoice line when its
invoice is, an audit step when its run is. Policies read the parent through
row-level security of their own, so this cannot be widened by accident. The
payment-provider event log has no tenant at all -- it is the de-duplication
record for incoming webhooks -- and is visible only in system context.

FORCE, so the policy binds the table owner too. Superusers still bypass row
security by design; the services no longer connect as one (stage 1).
"""

from __future__ import annotations

from alembic import op

revision: str = "0028_finance_rls"
down_revision: str | None = "0027_expenses_owners"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"

#: Secured through a parent row, not an organisation column.
CHILD_POLICIES = {
    "folio_entry_taxes":
        "EXISTS (SELECT 1 FROM finance.folio_entries p WHERE p.id = folio_entry_id)",
    "invoice_lines":
        "EXISTS (SELECT 1 FROM finance.invoices p WHERE p.id = invoice_id)",
    "night_audit_steps":
        "EXISTS (SELECT 1 FROM finance.night_audit_runs p WHERE p.id = run_id)",
    "folio_number_counters":
        "EXISTS (SELECT 1 FROM iam.properties p WHERE p.id = property_id"
        " AND tenancy.org_visible(p.organization_id))",
}

#: No tenant at all; only the jobs that work across tenants may see it.
SYSTEM_ONLY = {"provider_events": "tenancy.system_context()"}

FUNCTIONS = """
SELECT pg_advisory_xact_lock(hashtext('chirala:tenancy-functions'));

CREATE SCHEMA IF NOT EXISTS tenancy;

CREATE OR REPLACE FUNCTION tenancy.system_context() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('app.system', true), '') = 'on'
$$;

CREATE OR REPLACE FUNCTION tenancy.org_visible(org uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT tenancy.system_context()
        OR (org IS NOT NULL
            AND org::text = nullif(current_setting('app.organization_id', true), ''))
$$;
"""


def _tables_with_org() -> list[str]:
    rows = op.get_bind().exec_driver_sql(
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'finance' AND c.relkind = 'r'
          AND EXISTS (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'organization_id'
                        AND NOT a.attisdropped)
        ORDER BY c.relname
        """
    ).fetchall()
    return [r[0] for r in rows]


def _all_tables() -> list[str]:
    rows = op.get_bind().exec_driver_sql(
        """
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'finance' AND c.relkind = 'r'
          AND c.relname <> 'alembic_version'
        """
    ).fetchall()
    return [r[0] for r in rows]


def _secure(table: str, expression: str) -> None:
    op.execute(f"ALTER TABLE finance.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE finance.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON finance.{table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON finance.{table} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )


def upgrade() -> None:
    op.execute(FUNCTIONS)
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT USAGE ON SCHEMA tenancy TO {RUNTIME_ROLE};
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA tenancy TO {RUNTIME_ROLE};
          END IF;
        END
        $$
    """)

    with_org = _tables_with_org()
    for table in with_org:
        _secure(table, "tenancy.org_visible(organization_id)")
    for table, expression in {**CHILD_POLICIES, **SYSTEM_ONLY}.items():
        _secure(table, expression)

    # Every finance table must now be covered. A table added without a tenant
    # column and without a policy here would be readable by every tenant, so
    # the migration refuses to finish rather than leave one.
    covered = set(with_org) | set(CHILD_POLICIES) | set(SYSTEM_ONLY)
    missing = sorted(set(_all_tables()) - covered)
    if missing:
        raise RuntimeError(f"finance tables with no tenant policy: {missing}")


def downgrade() -> None:
    for table in _all_tables():
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON finance.{table}")
        op.execute(f"ALTER TABLE finance.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE finance.{table} DISABLE ROW LEVEL SECURITY")
