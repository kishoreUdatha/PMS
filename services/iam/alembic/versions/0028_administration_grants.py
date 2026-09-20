"""Grant administration.view and .configure, so a tenant can see its own plan

Revision ID: 0028_administration_grants
Revises: 0027_billing_schema
Create Date: 2026-09-14

The eighth of these, and the same shape as 0021, 0022 and 0023: a permission
the catalogue defines that no role has ever been granted.

``administration`` has seven actions in ``iam.permissions`` and is held by
**nobody** -- not Administrator, not Property IT Administrator, not any of the
twelve roles in this deployment. Every route gated on it therefore denies
every user in every tenant, and the tenant-facing billing routes added with
0027 walked straight into it: a property owner asking what plan they are on
got ``403 Permission denied: administration.view`` on their own subscription.

It went unnoticed the way the previous three did. A missing grant is
indistinguishable from a deliberate denial until somebody clicks the button,
and until 0027 nothing was gated on this resource at all.

  Administrator               view + configure -- the organisation's own
                              administrator, who signs up and chooses the plan
  Property IT Administrator   view + configure -- the role created by
                              self-signup, so the person who registered the
                              property can see what they are paying for

Deliberately nobody else. A front desk clerk has no business reading the
company's software bill, and the departmental roles keep the reports and
payments permissions they already hold -- those are guest money, which is a
different question from what the property pays us.

Roles are per-organisation here, so each tenant's own copy of a role is
granted. Roles are matched by name and skipped where a deployment lacks them.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028_administration_grants"
down_revision: str | None = "0027_billing_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS: dict[str, tuple[str, ...]] = {
    "Administrator": ("view", "configure"),
    "Property IT Administrator": ("view", "configure"),
}


def upgrade() -> None:
    for role_name, actions in GRANTS.items():
        acts = ", ".join(f"'{a}'" for a in actions)
        op.execute(
            f"""
            INSERT INTO iam.role_permissions
                (role_id, permission_id, record_scope)
            SELECT r.id, p.id, 'property'
            FROM iam.roles r
            JOIN iam.permissions p
              ON p.resource_code = 'administration'
             AND p.action_code IN ({acts})
            WHERE r.name = '{role_name}'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )


def downgrade() -> None:
    names = ", ".join(f"'{n}'" for n in GRANTS)
    op.execute(
        f"""
        DELETE FROM iam.role_permissions rp
        USING iam.roles r, iam.permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND p.resource_code = 'administration'
          AND p.action_code IN ('view', 'configure')
          AND r.name IN ({names})
        """
    )
