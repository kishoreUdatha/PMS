"""Grant the front_desk permissions to the roles that should hold them

Revision ID: 0008_front_desk_grants
Revises: 0007_payments_grants
Create Date: 2026-09-09

The same gap 0007 closed for ``payments``, now for ``front_desk``. Only
Administrator held any of these, and only ``view`` and ``create`` — so the role
actually named **Front Desk** could not have checked a guest in, and neither
could a manager.

Guest Check-In (screen 005) is the first screen to guard anything with
``front_desk``, which is how it surfaced.

  Front Desk       view, create, edit, cancel — arrivals, check-in, and undoing
                   a mistake at the desk
  Reservations     view — see who is arriving without working the desk
  Housekeeping     view — knowing which rooms are being checked into is the
                   whole basis of the cleaning order
  Resort Manager   everything, including approve and export
  Administrator    everything (it already had view/create)
  Property IT Administrator
                   everything — the property superuser role this deployment
                   signs in as

Roles are matched by name and simply skipped where a deployment does not have
them, so this INSERT never assumes a particular seed.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_front_desk_grants"
down_revision: str | None = "0007_payments_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL = ("view", "create", "edit", "cancel", "approve", "export", "configure")

GRANTS: dict[str, tuple[str, ...]] = {
    "Front Desk": ("view", "create", "edit", "cancel"),
    "Reservations": ("view",),
    "Housekeeping": ("view",),
    "Resort Manager": ALL,
    "Administrator": ALL,
    "Property IT Administrator": ALL,
}


def upgrade() -> None:
    for role_name, actions in GRANTS.items():
        op.execute(
            f"""
            INSERT INTO iam.role_permissions (role_id, permission_id, record_scope)
            SELECT r.id, p.id, 'property'
            FROM iam.roles r
            JOIN iam.permissions p
              ON p.resource_code = 'front_desk'
             AND p.action_code IN ({", ".join(f"'{a}'" for a in actions)})
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
          AND p.resource_code = 'front_desk'
          AND r.name IN ({names})
        """
    )
