"""Grant housekeeping so the Housekeeping board can be guarded

Revision ID: 0010_housekeeping_grants
Revises: 0009_core_grants
Create Date: 2026-09-09

The fourth of these, and the pattern is by now familiar: all seven
``housekeeping`` permission rows have existed since the first IAM seed and are
granted to **no role at all** — including the role literally named
Housekeeping, which is assigned to a real user. Nobody noticed because nothing
asked for them.

SCR-006 asks for them, so they have to be granted or the board 403s for
everyone, the housekeeper included.

  Housekeeping     view/create/edit/cancel/export — the department runs its own
                   board, but does not sign off inspections or change setup
  Front Desk       view — the desk sells rooms and needs to know which are
                   ready; it does not clean them
  Resort Manager   everything, including approve (inspection sign-off)
  Administrator    everything
  Property IT Administrator
                   everything

Roles are matched by name and skipped where a deployment lacks them.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_housekeeping_grants"
down_revision: str | None = "0009_core_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL = ("view", "create", "edit", "cancel", "approve", "export", "configure")

GRANTS: dict[str, tuple[str, ...]] = {
    "Housekeeping": ("view", "create", "edit", "cancel", "export"),
    "Front Desk": ("view",),
    "Resort Manager": ALL,
    "Administrator": ALL,
    "Property IT Administrator": ALL,
}


def upgrade() -> None:
    for role_name, actions in GRANTS.items():
        op.execute(
            f"""
            INSERT INTO iam.role_permissions
                (role_id, permission_id, record_scope)
            SELECT r.id, p.id, 'property'
            FROM iam.roles r
            JOIN iam.permissions p
              ON p.resource_code = 'housekeeping'
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
          AND p.resource_code = 'housekeeping'
          AND r.name IN ({names})
        """
    )
