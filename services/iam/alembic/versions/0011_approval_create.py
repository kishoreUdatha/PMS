"""Let approval requests be raised, and give finance two policies to route by

Revision ID: 0011_approval_create
Revises: 0010_housekeeping_grants
Create Date: 2026-09-09

Screen 042 has been able to *list* and *decide* approval requests since it was
built. It has never been able to raise one. ``iam.permissions`` reflects that
exactly: ``approval`` has view, decide and manage, and no create — because
nothing created anything.

The consequence has been showing up in every screen since. A waiver over
threshold, an upgrade over threshold, a cancellation over threshold: each is
recorded in its own table as ``pending_approval`` and then simply sits there,
because there is no queue entry to decide and no way to make one. This adds the
permission that the create endpoint asks for, and grants it to the people who
actually initiate things.

**Two new policies**, because the folio screens need something to route by:

  Folio adjustments above 10,000   the rule mockup 114 states in so many words
  Payment reversals above 5,000    reversing settled money is not a refund; the
                                   original payment stays and a reversing entry
                                   is posted against it, which is a different
                                   decision and deserves its own rule

Approver roles name roles that exist in this deployment. The mockups say
"Finance Manager", which is not a role anybody holds here; writing it down
would produce a policy that routes to nobody.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_approval_create"
down_revision: str | None = "0010_housekeeping_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Who may raise a request. Deliberately wide: raising is not deciding, and a
# desk agent who cannot ask for a sign-off will find a way around the rule.
INITIATORS = (
    "Front Desk", "Reservations", "Housekeeping", "Outlet Manager",
    "Resort Manager", "Administrator", "Property IT Administrator",
)


# The policy vocabulary was fixed at four categories when approvals were first
# built. ``adjustment`` and ``reversal`` are what the folio screens route by;
# ``waiver``, ``upgrade`` and ``cancellation`` are already recorded as
# ``pending_approval`` in booking-core's own tables with no queue entry to match
# them, so naming them here costs nothing and stops a later migration.
CATEGORIES = (
    "discount", "refund", "rate_override", "adjustment", "reversal",
    "waiver", "upgrade", "cancellation", "other",
)


def upgrade() -> None:
    op.execute("ALTER TABLE iam.approval_policies DROP CONSTRAINT ck_policy_category")
    op.execute(
        "ALTER TABLE iam.approval_policies ADD CONSTRAINT ck_policy_category "
        "CHECK (category IN (" + ", ".join(f"'{c}'" for c in CATEGORIES) + "))"
    )
    op.execute(
        """
        INSERT INTO iam.permissions (id, resource_code, action_code, description)
        SELECT gen_random_uuid(), 'approval', 'create',
               'Raise an approval request'
        WHERE NOT EXISTS (
            SELECT 1 FROM iam.permissions
            WHERE resource_code = 'approval' AND action_code = 'create'
        )
        """
    )
    names = ", ".join(f"'{n}'" for n in INITIATORS)
    op.execute(
        f"""
        INSERT INTO iam.role_permissions (role_id, permission_id, record_scope)
        SELECT r.id, p.id, 'property'
        FROM iam.roles r
        JOIN iam.permissions p
          ON p.resource_code = 'approval' AND p.action_code = 'create'
        WHERE r.name IN ({names})
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )

    # One policy per finance category, per organisation that has none.
    for category, name, applies, threshold, approvers in (
        ("adjustment", "Folio adjustments above 10,000",
         "Corrections, discounts and allowances posted against a guest folio",
         10000, '["Resort Manager", "Administrator"]'),
        ("reversal", "Payment reversals above 5,000",
         "Reversing a settled payment after the day it was taken",
         5000, '["Resort Manager", "Administrator"]'),
    ):
        op.execute(
            f"""
            INSERT INTO iam.approval_policies
                (id, organization_id, name, category, applies_to,
                 initiator_roles, approver_roles, threshold_value,
                 threshold_unit, two_level, prohibit_self_approval, active)
            SELECT gen_random_uuid(), o.id, '{name}', '{category}', '{applies}',
                   -- Both role lists are jsonb on this table, not text[].
                   '["Front Desk", "Reservations", "Outlet Manager"]'::jsonb,
                   '{approvers}'::jsonb,
                   {threshold}, 'INR', false, true, true
            FROM iam.organizations o
            WHERE NOT EXISTS (
                SELECT 1 FROM iam.approval_policies ap
                WHERE ap.organization_id = o.id AND ap.category = '{category}'
            )
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM iam.approval_policies
         WHERE category IN ('adjustment', 'reversal')
        """
    )
    op.execute("ALTER TABLE iam.approval_policies DROP CONSTRAINT ck_policy_category")
    op.execute(
        "ALTER TABLE iam.approval_policies ADD CONSTRAINT ck_policy_category "
        "CHECK (category IN ('discount', 'refund', 'rate_override', 'other'))"
    )
    op.execute(
        """
        DELETE FROM iam.role_permissions rp
        USING iam.permissions p
        WHERE rp.permission_id = p.id
          AND p.resource_code = 'approval' AND p.action_code = 'create'
        """
    )
    op.execute(
        "DELETE FROM iam.permissions "
        "WHERE resource_code = 'approval' AND action_code = 'create'"
    )
