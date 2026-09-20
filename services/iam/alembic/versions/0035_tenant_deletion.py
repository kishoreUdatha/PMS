"""A supported way to delete a tenant.

Deleting a tenant had no endpoint at all. That was a defensible default --
suspension is reversible, a hotel's folios are their commercial records, and
the work is genuinely not one statement: 102 tables carry an organisation or
property scope, only 2 of 23 foreign keys into iam cascade, and the
booking/finance/engagement schemas hold no foreign key to iam whatsoever.

But "no endpoint" is not the same as "does not happen". It happened by hand,
which meant no capability check, no reason, no approval and no audit row --
every safeguard the console applies to *suspending* a tenant, absent from the
larger act. This migration gives the operation somewhere to live.

Shaped after platform.restore_requests, which already solves the same problem
for restores: request and approval are separate acts by separate people, and
the database refuses them being the same person rather than trusting the UI.

Two capabilities, not one. `tenant.delete` asks; `tenant.delete_approve`
carries it out. Neither is granted to platform_operator -- the role most staff
hold -- so the default remains that nobody can do this.
"""
from alembic import op

revision = "0035_tenant_deletion"
down_revision = "0034_booking_engine_in_plans"
branch_labels = None
depends_on = None


TABLE = """
CREATE TABLE IF NOT EXISTS platform.tenant_deletions (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Deliberately NOT a foreign key to iam.organizations. The row has to
    -- outlive the tenant it describes: a deletion record that disappears with
    -- its subject records nothing. The name and code are copied for the same
    -- reason -- afterwards they are the only trace of which tenant this was.
    organization_id   uuid NOT NULL,
    organization_code varchar(12),
    organization_name varchar(200) NOT NULL,

    reason            varchar(400) NOT NULL,
    requested_by      uuid NOT NULL REFERENCES iam.users(id),
    requested_at      timestamptz NOT NULL DEFAULT now(),

    -- Nothing may be carried out before this moment. A deletion that can be
    -- completed in the same minute it was thought of is one angry afternoon
    -- away from being permanent.
    executable_after  timestamptz NOT NULL,

    status            varchar(16) NOT NULL DEFAULT 'requested',
    approved_by       uuid REFERENCES iam.users(id),
    approved_at       timestamptz,
    completed_at      timestamptz,

    -- What was actually removed, table by table, and where the export went.
    removed           jsonb NOT NULL DEFAULT '{}'::jsonb,
    export_location   varchar(400),

    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_deletion_status CHECK (status IN
        ('requested', 'approved', 'refused', 'completed', 'cancelled')),

    -- The rule the whole design rests on, held by the database rather than by
    -- a handler somebody may later refactor.
    CONSTRAINT ck_deletion_separate_approver
        CHECK (approved_by IS NULL OR approved_by <> requested_by),

    CONSTRAINT ck_deletion_approved_has_approver
        CHECK (status NOT IN ('approved', 'completed')
               OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);

-- One live request per tenant. Asking twice is not more deletion.
CREATE UNIQUE INDEX IF NOT EXISTS uq_deletion_open_per_org
    ON platform.tenant_deletions (organization_id)
    WHERE status IN ('requested', 'approved');

CREATE INDEX IF NOT EXISTS ix_deletion_status
    ON platform.tenant_deletions (status, requested_at DESC);

ALTER TABLE platform.tenant_deletions ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.tenant_deletions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_deletions_read ON platform.tenant_deletions;
CREATE POLICY tenant_deletions_read ON platform.tenant_deletions
    FOR SELECT USING (tenancy.system_context());

DROP POLICY IF EXISTS tenant_deletions_write ON platform.tenant_deletions;
CREATE POLICY tenant_deletions_write ON platform.tenant_deletions
    FOR ALL USING (tenancy.system_context())
    WITH CHECK (tenancy.system_context());
"""

CAPABILITIES = [
    ("tenant.delete", "Request permanent deletion of a tenant and its data"),
    ("tenant.delete_approve",
     "Approve and carry out a tenant deletion raised by somebody else"),
]


def upgrade() -> None:
    op.execute(TABLE)

    for code, description in CAPABILITIES:
        op.execute(
            f"""
            INSERT INTO iam.platform_permissions (code, description)
            VALUES ('{code}', '{description}')
            ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description
            """
        )

    # Only the owner. Not platform_operator, which is the role most staff
    # carry -- the point of the capability is that holding it is unusual.
    for code, _ in CAPABILITIES:
        op.execute(
            f"""
            INSERT INTO iam.platform_role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM iam.platform_roles r, iam.platform_permissions p
            WHERE r.code = 'platform_owner' AND p.code = '{code}'
            ON CONFLICT DO NOTHING
            """
        )

    # A migration that silently granted nothing would leave the endpoints
    # unreachable and look like a permissions bug later.
    op.execute(
        """
        DO $$
        DECLARE granted int;
        BEGIN
            SELECT count(*) INTO granted
            FROM iam.platform_role_permissions rp
            JOIN iam.platform_permissions p ON p.id = rp.permission_id
            JOIN iam.platform_roles r ON r.id = rp.role_id
            WHERE p.code IN ('tenant.delete', 'tenant.delete_approve')
              AND r.code = 'platform_owner';
            IF granted <> 2 THEN
                RAISE EXCEPTION
                    'tenant deletion capabilities not granted to platform_owner (got %)',
                    granted;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.tenant_deletions")
    op.execute(
        """
        DELETE FROM iam.platform_role_permissions
        WHERE permission_id IN (SELECT id FROM iam.platform_permissions
                                WHERE code IN ('tenant.delete',
                                               'tenant.delete_approve'))
        """
    )
    op.execute(
        "DELETE FROM iam.platform_permissions "
        "WHERE code IN ('tenant.delete', 'tenant.delete_approve')"
    )
