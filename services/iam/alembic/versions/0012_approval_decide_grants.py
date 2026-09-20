"""Give the roles the policies name the right to actually decide

Revision ID: 0012_approval_decide_grants
Revises: 0011_approval_create
Create Date: 2026-09-09

Every approval policy in this system names business roles as its approvers —
General Manager, Front Office Manager, and now Resort Manager and
Administrator. Not one of them holds ``approval.view`` or ``approval.decide``.
The only role that does is Property IT Administrator, a technical role that has
no business standing to approve a refund.

So the queue built for screen 042 has been, for every manager in the property,
invisible; and the policies routing work to them have been routing it into
nothing. That was survivable while nothing could raise a request. It stops
being survivable in the same migration that lets requests be raised, which is
why this follows immediately.

  Resort Manager, Administrator   view + decide — the roles the finance
                                  policies name as approvers
  Front Desk, Reservations,
  Outlet Manager, Housekeeping    view only — an initiator should be able to
                                  see where their own request got to, and not
                                  decide it

``prohibit_self_approval`` on the policy still stops anyone approving their own
request, so widening who can decide does not weaken the separation of duties;
it is what makes the separation possible at all, since previously the one role
that could decide was also the one raising everything.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_approval_decide_grants"
down_revision: str | None = "0011_approval_create"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANTS: dict[str, tuple[str, ...]] = {
    "Resort Manager": ("view", "decide"),
    "Administrator": ("view", "decide"),
    "Front Desk": ("view",),
    "Reservations": ("view",),
    "Outlet Manager": ("view",),
    "Housekeeping": ("view",),
}


def upgrade() -> None:
    for role_name, actions in GRANTS.items():
        op.execute(
            f"""
            INSERT INTO iam.role_permissions (role_id, permission_id, record_scope)
            SELECT r.id, p.id, 'property'
            FROM iam.roles r
            JOIN iam.permissions p
              ON p.resource_code = 'approval'
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
          AND p.resource_code = 'approval'
          AND p.action_code IN ('view', 'decide')
          AND r.name IN ({names})
        """
    )
