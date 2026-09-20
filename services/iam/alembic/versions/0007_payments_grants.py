"""Grant the payments permissions to the roles that should hold them

Revision ID: 0007_payments_grants
Revises: 0006_approvals
Create Date: 2026-09-09

The ``payments`` permission rows have existed since 0001 but were never granted
to a single role, so every check against them denied everyone. Nothing noticed
until the Deposit Schedule (screen 058) became the first screen to guard money
with them.

Deny-by-default is the point of the model, so the fix is to say who *should*
hold these, not to fall back on a permission that happens to be granted:

  Front Desk       take and record payments, adjust a schedule (no approve,
                   no reversals — a refund is not a front-desk decision)
  Reservations     see what a booking owes, without touching the money
  Resort Manager   the full set, including approving waivers and reversing
                   payments; this is the "Front Office Manager or General
                   Manager" the waiver rule on screen 058 refers to
  Administrator    the full set
  Property IT Administrator
                   the property superuser role in this deployment (it already
                   holds role manage, user create and approval decide), so it
                   gets the full set too

Roles are per-organization rows and are matched by name, which is how the seed
data names them. A role a given deployment does not have is simply skipped —
this INSERT joins, it does not assume.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_payments_grants"
down_revision: str | None = "0006_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS: dict[str, tuple[str, ...]] = {
    "Front Desk": ("view", "create", "edit"),
    "Reservations": ("view",),
    "Resort Manager": ("view", "create", "edit", "cancel", "approve", "export"),
    "Administrator": ("view", "create", "edit", "cancel", "approve", "export",
                      "configure"),
    "Property IT Administrator": ("view", "create", "edit", "cancel", "approve",
                                  "export", "configure"),
}


def upgrade() -> None:
    for role_name, actions in GRANTS.items():
        op.execute(
            f"""
            INSERT INTO iam.role_permissions (role_id, permission_id, record_scope)
            SELECT r.id, p.id, 'property'
            FROM iam.roles r
            JOIN iam.permissions p
              ON p.resource_code = 'payments'
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
          AND p.resource_code = 'payments'
          AND r.name IN ({names})
        """
    )
