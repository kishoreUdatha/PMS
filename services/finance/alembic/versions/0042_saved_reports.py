"""Saved custom reports.

Revision ID: 0042_saved_reports
Revises: 0041_pos

The report builder lets staff choose a dataset, its columns, filters, a
grouping and a sort, and run it. A saved report is that choice, kept so it
can be run again next month. Only the definition is stored, never the rows:
a saved report shows what the data says when it is run, not a copy of an
older answer.
"""
from __future__ import annotations

from alembic import op

revision: str = "0042_saved_reports"
down_revision: str | None = "0041_pos"
branch_labels: str | None = None
depends_on: str | None = None

RUNTIME_ROLE = "pms_app"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS finance.saved_reports (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            name            varchar(120) NOT NULL,
            dataset         varchar(40) NOT NULL,
            definition      jsonb NOT NULL,
            created_by      uuid NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_saved_report_name UNIQUE (property_id, name)
        );
        ALTER TABLE finance.saved_reports ENABLE ROW LEVEL SECURITY;
        ALTER TABLE finance.saved_reports FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS tenant_isolation ON finance.saved_reports;
        CREATE POLICY tenant_isolation ON finance.saved_reports
            USING (tenancy.org_visible(organization_id))
            WITH CHECK (tenancy.org_visible(organization_id));
    """)
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON finance.saved_reports TO {RUNTIME_ROLE};
          END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS finance.saved_reports")
