"""A short tenant code, because people read it out

Revision ID: 0030_tenant_code
Revises: 0029_platform_mfa
Create Date: 2026-09-14

The design pack's tenant directory leads with a ``Tenant ID`` column --
TN-001, TN-002 -- and there is nothing in the schema to put in it. An
organisation has a name and a uuid, and neither works: two customers can be
called "Coral Coast", and nobody reads a uuid down a phone.

Properties already solved this with a six-digit ``code`` (0018) for exactly
the same reason. This is the organisation's equivalent, and deliberately a
different shape -- TN-007 against 559167 -- so that a code quoted in a support
ticket is unambiguous about what it identifies.

Sequential rather than random. A tenant code is not a secret: it appears in
the directory, in tickets and in invoices, and guessing TN-008 tells an
attacker nothing they could not get by counting. What sequence buys is that
the codes sort in signup order, which is how the directory is read.

Backfilled in creation order, so the tenants that already exist get the
numbers they would have had.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030_tenant_code"
down_revision: str | None = "0029_platform_mfa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE iam.organizations ADD COLUMN IF NOT EXISTS "
        "code varchar(12) NULL"
    )
    # In creation order, so existing tenants get the numbers they would have
    # been given had the column always been here.
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS n
            FROM iam.organizations
        )
        UPDATE iam.organizations o
           SET code = 'TN-' || lpad(ordered.n::text, 3, '0')
          FROM ordered
         WHERE ordered.id = o.id AND o.code IS NULL
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_organization_code "
        "ON iam.organizations (code)"
    )

    # New organisations get one automatically. A trigger rather than
    # application code because tenants are created in three places -- public
    # sign-up, the platform console, and fixtures -- and a column that is
    # sometimes filled is worse than one that never is.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION iam.assign_organization_code()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.code IS NULL THEN
                -- Serialised on one advisory lock so two concurrent sign-ups
                -- cannot read the same maximum and both claim it.
                PERFORM pg_advisory_xact_lock(hashtext('iam:tenant-code'));
                SELECT 'TN-' || lpad((coalesce(max(
                           nullif(regexp_replace(code, '\\D', '', 'g'), '')::int
                       ), 0) + 1)::text, 3, '0')
                  INTO NEW.code
                  FROM iam.organizations;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_organization_code "
               "ON iam.organizations")
    op.execute(
        "CREATE TRIGGER trg_organization_code BEFORE INSERT ON "
        "iam.organizations FOR EACH ROW "
        "EXECUTE FUNCTION iam.assign_organization_code()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_organization_code ON iam.organizations")
    op.execute("DROP FUNCTION IF EXISTS iam.assign_organization_code()")
    op.execute("DROP INDEX IF EXISTS iam.uq_organization_code")
    op.execute("ALTER TABLE iam.organizations DROP COLUMN IF EXISTS code")
