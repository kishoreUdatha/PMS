"""Seed and grant property.create, so a second property can be added

Revision ID: 0023_property_create_grant
Revises: 0022_reports_grants
Create Date: 2026-09-13

The seventh of these, and the first where the permission row itself was never
seeded at all.

``POST /properties`` is gated on ``property:create``. The catalogue has exactly
two ``property`` rows -- ``view`` and ``update`` -- so the permission the route
demands has never existed, and ``_check`` denies what it cannot find. Every
user in every tenant gets ``403 Permission denied: property.create``, including
Property IT Administrator, which is the role that manages properties. A tenant
that onboarded with one property has had no way to add a second.

It went unnoticed the same way 0021 and 0022 did: the gate was added when the
route was hardened -- its docstring still describes the bug it was closing,
where ``organization_id`` came from the body unauthenticated -- and the
catalogue was not updated to match. Nothing failed at boot, because a missing
permission is indistinguishable from an ungranted one until somebody clicks
the button.

  Administrator     view/update/create -- the organisation administrator held
                    *no* property permissions whatsoever, which is its own gap;
                    it is the role that should be able to see and add them
  Property IT Administrator
                    create -- it already has view and update

Deliberately nobody else. Creating a property is organisation-level
administration, not something a manager of one property does; Resort Manager
and the departmental roles keep the view/update they have through other
resources.

Roles are matched by name and skipped where a deployment lacks them. Roles are
per-organisation here, so each tenant's own copy of a role is granted.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023_property_create_grant"
down_revision: str | None = "0022_reports_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS: dict[str, tuple[str, ...]] = {
    "Administrator": ("view", "update", "create"),
    "Property IT Administrator": ("create",),
}


def upgrade() -> None:
    # The permission the route has been asking for since it was hardened.
    op.execute(
        """
        INSERT INTO iam.permissions (resource_code, action_code, description)
        VALUES ('property', 'create', 'Create a property in the organisation')
        ON CONFLICT (resource_code, action_code) DO NOTHING
        """
    )

    for role_name, actions in GRANTS.items():
        op.execute(
            f"""
            INSERT INTO iam.role_permissions
                (role_id, permission_id, record_scope)
            SELECT r.id, p.id, 'property'
            FROM iam.roles r
            JOIN iam.permissions p
              ON p.resource_code = 'property'
             AND p.action_code IN ({", ".join(f"'{a}'" for a in actions)})
            WHERE r.name = '{role_name}'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )


def downgrade() -> None:
    # The grants this migration added, and only the create permission row --
    # view and update predate it and other roles rely on them.
    names = ", ".join(f"'{n}'" for n in GRANTS)
    op.execute(
        f"""
        DELETE FROM iam.role_permissions rp
        USING iam.roles r, iam.permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND p.resource_code = 'property'
          AND p.action_code = 'create'
          AND r.name IN ({names})
        """
    )
    op.execute(
        """
        DELETE FROM iam.role_permissions rp
        USING iam.roles r, iam.permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND p.resource_code = 'property'
          AND p.action_code IN ('view', 'update')
          AND r.name = 'Administrator'
        """
    )
    op.execute(
        """
        DELETE FROM iam.permissions
        WHERE resource_code = 'property' AND action_code = 'create'
        """
    )
