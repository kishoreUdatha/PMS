"""Grant guests / reservations / dashboard so the pre-library routes can be guarded

Revision ID: 0009_core_grants
Revises: 0008_front_desk_grants
Create Date: 2026-09-09

The third and last of these. ``payments`` (0007) and ``front_desk`` (0008) had
permission rows granted to nobody; the same is true of ``guests`` — zero roles —
while ``reservations`` and ``dashboard`` reach only one or two.

That did not matter while the routes behind them were unguarded, which is
exactly the problem: the reservations, guests, folio, charge and payment
endpoints have been open since they were written, before ``chirala_common``
existed. Guarding them is the point of this change, and guarding them without
granting first would lock every role out of the product.

  Front Desk       guests view/create/edit, reservations view/create/edit,
                   dashboard view — the desk creates guests and bookings
  Reservations     reservations everything short of configure; guests
                   view/create/edit; dashboard view
  Reservations,
  Front Desk,
  Housekeeping     dashboard view — everyone who works a shift reads it
  Resort Manager   all of guests, reservations and dashboard
  Administrator    the same
  Property IT Administrator
                   the same, plus the rooms actions it was missing

Roles are matched by name and skipped where a deployment lacks them.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_core_grants"
down_revision: str | None = "0008_front_desk_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL = ("view", "create", "edit", "cancel", "approve", "export", "configure")
RW = ("view", "create", "edit")

# role -> resource -> actions
GRANTS: dict[str, dict[str, tuple[str, ...]]] = {
    "Front Desk": {
        "guests": RW, "reservations": RW, "dashboard": ("view",),
    },
    "Reservations": {
        "guests": RW, "dashboard": ("view",),
        "reservations": ("view", "create", "edit", "cancel", "export"),
    },
    "Housekeeping": {"dashboard": ("view",)},
    "Resort Manager": {
        "guests": ALL, "reservations": ALL, "dashboard": ALL,
    },
    "Administrator": {
        "guests": ALL, "reservations": ALL, "dashboard": ALL, "rooms": ALL,
    },
    "Property IT Administrator": {
        "guests": ALL, "reservations": ALL, "dashboard": ALL,
        # It held rooms view/create/edit/export/configure but not cancel or
        # approve, which the guarded room endpoints now ask for.
        "rooms": ALL,
    },
}


def upgrade() -> None:
    for role_name, resources in GRANTS.items():
        for resource, actions in resources.items():
            op.execute(
                f"""
                INSERT INTO iam.role_permissions
                    (role_id, permission_id, record_scope)
                SELECT r.id, p.id, 'property'
                FROM iam.roles r
                JOIN iam.permissions p
                  ON p.resource_code = '{resource}'
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
          AND p.resource_code IN ('guests', 'reservations', 'dashboard')
          AND r.name IN ({names})
        """
    )
