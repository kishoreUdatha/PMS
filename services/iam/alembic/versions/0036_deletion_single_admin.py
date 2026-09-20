"""Let a one-admin deployment finish a tenant deletion.

0035 held the two-person rule in a CHECK: ``approved_by <> requested_by``.
Together with ``ck_deletion_approved_has_approver`` -- a completed row must
have an approver -- that makes completion arithmetically impossible where
there is only one platform admin. Nobody else exists to be the approver, and
the row cannot be marked completed without one. The feature was unusable in
exactly the deployments most likely to need it, and the failure was silent
until somebody actually tried.

The rule is real and stays. It just cannot live in a CHECK, because whether a
second approver *exists* is a fact about another table, which a CHECK cannot
see. It moves to the route, which counts the active platform admins and
insists on a separate approver whenever there is one to insist on. A
deployment that grows a second admin gets the rule back automatically, with no
migration and nothing to remember.

What stays in the schema is the part a CHECK can honestly enforce: a completed
deletion names who approved it and when.
"""
from alembic import op

revision = "0036_deletion_single_admin"
down_revision = "0035_tenant_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.tenant_deletions "
        "DROP CONSTRAINT IF EXISTS ck_deletion_separate_approver"
    )
    # The remaining guarantee, stated again so it is obvious this was not
    # simply loosened: completion is always attributable.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_deletion_approved_has_approver'
            ) THEN
                RAISE EXCEPTION
                    'ck_deletion_approved_has_approver is missing; a completed '
                    'deletion would no longer have to name an approver';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.tenant_deletions
        ADD CONSTRAINT ck_deletion_separate_approver
        CHECK (approved_by IS NULL OR approved_by <> requested_by)
        """
    )
