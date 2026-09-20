"""Grant distribution so the booking engine can be switched on

Revision ID: 0021_distribution_grants
Revises: 0020_email_verification
Create Date: 2026-09-12

The fifth of these, and the story is the one from housekeeping again: all seven
``distribution`` permission rows have existed since the permission catalogue was
seeded and are granted to **no role at all** — including the role literally
named Distribution. Nobody noticed because nothing asked for them.

Something asks now. A property is not publicly bookable until it holds the
``booking_engine`` module entitlement, and the endpoint that grants it is gated
on ``distribution:configure``. Without these grants that endpoint 403s for
everybody, and no tenant could ever put itself on sale.

``distribution`` and not ``property:update``, which is the permission that would
otherwise have fitted: property.update is addresses and phone numbers, and
whoever corrects a phone number should not thereby be able to publish the hotel
to the internet. Distribution is the hotel word for the channels a property
sells through, which is exactly what this is.

  Distribution     view/create/edit/cancel/export — the department works the
                   channels, but does not decide whether the hotel is on sale
  Sales            view — sells against what is available; does not set it up
  Reservations     view — needs to know whether bookings can arrive online,
                   because that explains where they came from
  Rates            view — rates are what the engine quotes
  Resort Manager   everything, including configure (on sale / off sale)
  Administrator    everything
  Property IT Administrator
                   everything

Putting ``configure`` with management rather than with the Distribution
department is the same judgement 0010 made for inspection sign-off. If a
deployment wants its distribution team to own the switch, granting one more
permission is the whole change.

Roles are matched by name and skipped where a deployment lacks them.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_distribution_grants"
down_revision: str | None = "0020_email_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL = ("view", "create", "edit", "cancel", "approve", "export", "configure")

GRANTS: dict[str, tuple[str, ...]] = {
    "Distribution": ("view", "create", "edit", "cancel", "export"),
    "Sales": ("view",),
    "Reservations": ("view",),
    "Rates": ("view",),
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
              ON p.resource_code = 'distribution'
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
          AND p.resource_code = 'distribution'
          AND r.name IN ({names})
        """
    )
